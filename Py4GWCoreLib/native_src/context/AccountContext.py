
from ctypes import Structure, POINTER, c_uint32, cast, c_uint8
from ..internals.gw_array import GW_Array, GW_Array_Value_View


class AccountUnlockedCountStruct(Structure):
    _pack_ = 1
    _fields_ = [
        ("id", c_uint32),
        ("unk1", c_uint32),
        ("unk2", c_uint32),
    ]


class AccountUnlockedItemInfoStruct(Structure):
    _pack_ = 1
    _fields_ = [
        ("name_id", c_uint32),
        ("mod_struct_index", c_uint32),   # Used to find mod struct in unlocked_pvp_items_mod_structs
        ("mod_struct_size", c_uint32),
    ]


class AccountContextStruct(Structure):
    """GWCA: AccountContext (0x138 bytes)."""
    _pack_ = 1
    _fields_ = [
        ("account_unlocked_counts_array", GW_Array),             # +0x0000 Array<AccountUnlockedCount> (e.g. unlocked storage panes)
        ("h0010", c_uint8 * 0xA4),                               # +0x0010
        ("unlocked_pvp_heros_array", GW_Array),                  # +0x00B4 Array<u32> — unused since hero battles ended
        ("h00c4_array", GW_Array),                               # +0x00C4 Array<u32> — mod structs backing unlocked_pvp_item_info
        ("unlocked_pvp_item_info_array", GW_Array),              # +0x00E4 Array<AccountUnlockedItemInfo>
        ("unlocked_pvp_items_array", GW_Array),                  # +0x00F4 Array<u32> — bitwise unlocks
        ("h0104", c_uint8 * 0x30),                               # +0x0104 arrays + linked lists
        ("unlocked_account_skills_array", GW_Array),             # +0x0124 Array<u32> — hero-usable / tome-unlockable skills
        ("account_flags", c_uint32),                             # +0x0134
    ]

    @property
    def account_unlocked_counts(self) -> list[AccountUnlockedCountStruct]:
        return GW_Array_Value_View(self.account_unlocked_counts_array, AccountUnlockedCountStruct).to_list()

    @property
    def unlocked_pvp_heros(self) -> list[int]:
        return [int(x) for x in GW_Array_Value_View(self.unlocked_pvp_heros_array, c_uint32).to_list()]

    @property
    def unlocked_pvp_item_info(self) -> list[AccountUnlockedItemInfoStruct]:
        return GW_Array_Value_View(self.unlocked_pvp_item_info_array, AccountUnlockedItemInfoStruct).to_list()

    @property
    def unlocked_pvp_items(self) -> list[int]:
        return [int(x) for x in GW_Array_Value_View(self.unlocked_pvp_items_array, c_uint32).to_list()]

    @property
    def unlocked_account_skills(self) -> list[int]:
        return [int(x) for x in GW_Array_Value_View(self.unlocked_account_skills_array, c_uint32).to_list()]


class AccountContext:
    _cached_ctx: AccountContextStruct | None = None
    _callback_name = "AccountContext.UpdatePtr"

    @staticmethod
    def get_ptr() -> int:
        from .GameContext import GameContext
        game_ctx = GameContext.get_context()
        if game_ctx is None:
            return 0
        return int(game_ctx.account_context)

    @staticmethod
    def _update_ptr():
        ptr = AccountContext.get_ptr()
        if not ptr:
            AccountContext._cached_ctx = None
            return
        AccountContext._cached_ctx = cast(ptr, POINTER(AccountContextStruct)).contents

    @staticmethod
    def enable():
        import PyCallback
        PyCallback.PyCallback.Register(
            AccountContext._callback_name,
            PyCallback.Phase.PreUpdate,
            AccountContext._update_ptr,
            priority=6,
            context=PyCallback.Context.Draw,
        )

    @staticmethod
    def disable():
        import PyCallback
        PyCallback.PyCallback.RemoveByName(AccountContext._callback_name)
        AccountContext._cached_ctx = None

    @staticmethod
    def get_context() -> AccountContextStruct | None:
        return AccountContext._cached_ctx


AccountContext.enable()
