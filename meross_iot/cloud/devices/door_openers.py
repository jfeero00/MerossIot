from meross_iot.cloud.abilities import *
from meross_iot.cloud.device import AbstractMerossDevice
from meross_iot.logger import POWER_PLUGS_LOGGER as l
from meross_iot.meross_event import DeviceDoorStatusEvent
from threading import Event

class GenericGarageDoorOpener(AbstractMerossDevice):
    # Channels
    _channels = []

    # Dictionary {channel_id (door) -> status}
    _door_state = None

    def __init__(self, cloud_client, device_uuid, **kwords):
        super(GenericGarageDoorOpener, self).__init__(cloud_client, device_uuid, **kwords)

    def get_status(self):
        with self._state_lock:
            if self._door_state is None:
                self._get_status_impl()

        return self._door_state

    def _handle_push_notification(self, namespace, payload, from_myself=False):
        def fire_garage_door_state_change(dev, channel_id, o_state, n_state, f_myself):
            if o_state != n_state:
                evt = DeviceDoorStatusEvent(dev=dev, channel_id=channel_id, door_state=n_state,
                                            generated_by_myself=f_myself)
                self.fire_event(evt)

        with self._state_lock:
            if namespace == GARAGE_DOOR_STATE:
                for door in payload['state']:
                    channel_index = door['channel']
                    state = door['open'] == 1
                    old_state = self._door_state[channel_index]
                    self._door_state[channel_index] = state
                    fire_garage_door_state_change(self, channel_index, old_state, state, from_myself)

            elif namespace == REPORT:
                # For now, we simply ignore push notification of these kind.
                # In the future, we might think of handling such notification by caching them
                # and avoid the network round-trip when asking for power consumption (if the latest report is
                # recent enough)
                pass

            else:
                l.error("Unknown/Unsupported namespace/command: %s" % namespace)

    def _get_status_impl(self):
        if self._door_state is None:
            self._door_state = {}
        data = self.get_sys_data()['all']
        if 'digest' in data:
            for c in data['digest']['garageDoor']:
                self._door_state[c['channel']] = c['open'] == 1
        return self._door_state

    def _get_channel_id(self, channel):
        # Otherwise, if the passed channel looks like the channel spec, lookup its array indexindex
        if channel in self._channels:
            return self._channels.index(channel)

        # if a channel name is given, lookup the channel id from the name
        if isinstance(channel, str):
            for i, c in enumerate(self.get_channels()):
                if c['devName'] == channel:
                    return c['channel']

        # If an integer is given assume that is the channel ID
        elif isinstance(channel, int):
            return channel

        # In other cases return an error
        raise Exception("Invalid channel specified.")

    def _operate_door(self, channel, state, callback, wait_for_sensor_confirmation):
        # If the door is already in the target status, do not execute the command.
        already_in_state = False
        with self._state_lock:
            already_in_state = self.get_status()[channel] == state

        if already_in_state and callback is None:
            l.info("Command was not executed: the door state is already %s" % ("open" if state else "closed"))
            return
        elif already_in_state and callback is not None:
            callback(None, self._door_state[channel])
            return

        payload = {"state": {"channel": channel, "open": state, "uuid": self.uuid}}
        if wait_for_sensor_confirmation:
            door_event = None
            if callback is None:
                door_event = Event()

            def waiter(data):
                self.unregister_event_callback(waiter)
                if data.channel != channel:
                    return
                if callback is None:
                    door_event.set()
                else:
                    if data.door_state != state:
                        callback("Operation failed", data.door_state)
                    else:
                        callback(None, data.door_sate)

            self.register_event_callback(waiter)
            self.execute_command(command="SET", namespace=GARAGE_DOOR_STATE, payload=payload, callback=None)

            if callback is None:
                door_event.wait()
                current_state = self._door_state[channel]
                if current_state != state:
                    raise Exception("Operation failed.")

        else:
            self.execute_command(command="SET", namespace=GARAGE_DOOR_STATE, payload=payload, callback=callback)

    def open_door(self, channel=0, callback=None, ensure_opened=True):
        c = self._get_channel_id(channel)
        return self._operate_door(c, 1, callback=callback, wait_for_sensor_confirmation=ensure_opened)

    def close_door(self, channel=0, callback=None, ensure_closed=True):
        c = self._get_channel_id(channel)
        return self._operate_door(c, 0, callback=callback, wait_for_sensor_confirmation=ensure_closed)

    def get_channels(self):
        return self._channels

    def __str__(self):
        base_str = super().__str__()
        with self._state_lock:
            if not self.online:
                return base_str
            doors = "Doors -> "
            doors += ",".join(["%d = %s" % (k, "OPEN" if v else "CLOSED") for k, v in enumerate(self.get_status())])
            return base_str + doors
