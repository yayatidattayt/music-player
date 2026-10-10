import unittest

from app.api.rooms import CreateRoomRequest, JoinRoomRequest, RoomManager


class RoomManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_and_join_room_assigns_unique_members(self):
        manager = RoomManager()
        room, host = await manager.create(CreateRoomRequest(name="Night radio", display_name="Host"))
        room_again, guest = await manager.join(JoinRoomRequest(code=room.code, display_name="Guest"))

        self.assertIs(room, room_again)
        self.assertNotEqual(host.member_id, guest.member_id)
        self.assertEqual(room.host_id, host.member_id)
        self.assertEqual(room.code, room.code.upper())
        self.assertEqual(room.members[guest.member_id].display_name, "Guest")

    async def test_cleanup_removes_only_expired_disconnected_rooms(self):
        manager = RoomManager()
        room, _ = await manager.create(CreateRoomRequest())
        room.updated_at -= 60 * 60 * 7
        await manager.cleanup()
        self.assertNotIn(room.room_id, manager.rooms)
        self.assertNotIn(room.code, manager.code_index)

    async def test_connected_room_survives_cleanup(self):
        manager = RoomManager()
        room, host = await manager.create(CreateRoomRequest())
        room.updated_at -= 60 * 60 * 7
        room.members[host.member_id].websocket = object()
        await manager.cleanup()
        self.assertIn(room.room_id, manager.rooms)


if __name__ == "__main__":
    unittest.main()
