from typing import Optional
from ctypes import Structure
from ..internals.gw_array import GW_Array


class AccountUnlockedCountStruct(Structure):
    id: int
    unk1: int
    unk2: int


class AccountUnlockedItemInfoStruct(Structure):
    name_id: int
    mod_struct_index: int
    mod_struct_size: int


class AccountContextStruct(Structure):
    """GWCA: AccountContext (0x138 bytes)."""
    account_unlocked_counts_array: GW_Array
    unlocked_pvp_heros_array: GW_Array
    h00c4_array: GW_Array
    unlocked_pvp_item_info_array: GW_Array
    unlocked_pvp_items_array: GW_Array
    unlocked_account_skills_array: GW_Array
    account_flags: int

    @property
    def account_unlocked_counts(self) -> list[AccountUnlockedCountStruct]: ...
    @property
    def unlocked_pvp_heros(self) -> list[int]: ...
    @property
    def unlocked_pvp_item_info(self) -> list[AccountUnlockedItemInfoStruct]: ...
    @property
    def unlocked_pvp_items(self) -> list[int]: ...
    @property
    def unlocked_account_skills(self) -> list[int]: ...


class AccountContext:
    @staticmethod
    def get_ptr() -> int: ...
    @staticmethod
    def _update_ptr() -> None: ...
    @staticmethod
    def enable() -> None: ...
    @staticmethod
    def disable() -> None: ...
    @staticmethod
    def get_context() -> Optional[AccountContextStruct]: ...
