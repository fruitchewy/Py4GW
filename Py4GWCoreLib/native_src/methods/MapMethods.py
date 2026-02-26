from ...Scanner import Scanner, ScannerSection
from ..internals.prototypes import Prototypes
from ..internals.native_function import NativeFunction
from ..internals.scan_resolver import resolve_scan
from ...UIManager import UIManager
from ...enums_src.UI_enums import UIMessage
from ..context.GuildContext import Guild, GuildContext, GHKey
import ctypes
from typing import List, Optional
from Py4GW import Game


# --- SkipCinematic ---
# Primary: assertion in CiCliApi.cpp (survives binary updates)
# Fallback: legacy byte pattern
_skip_addr = resolve_scan("SkipCinematic",
    lambda: Scanner.ToFunctionStart(
        Scanner.FindAssertion("CiCliApi.cpp", "context->script", 0, 0), 0xFFF))
SkipCinematic_Func: Optional[NativeFunction] = (
    NativeFunction.from_address(
        name="SkipCinematic_Func",
        address=_skip_addr,
        prototype=Prototypes["Void_NoArgs"],
    ) if _skip_addr else None
)


# --- AreaInfoArray ---
# GetAreaInfo body: bounds check → imul eax, <map_id>, 0x7C → add eax, <AreaInfoArray>
# We extract the 4-byte immediate from the add instruction.
def _resolve_area_info_assertion() -> int:
    assertion = Scanner.FindAssertion(
        "ConstMission.cpp", "index < arrsize(s_missionClientData)", 0, 0)
    if not assertion:
        return 0
    func_start = Scanner.ToFunctionStart(assertion, 0xFFF)
    if not func_start:
        return 0
    # add eax, <imm32> = opcode 0x05 followed by 4-byte address.
    # offset=1 lands on the immediate operand.
    return Scanner.FindInRange(b"\x05", "x", 1, func_start, func_start + 0x40)

_area_info_addr: int = resolve_scan("AreaInfoArray", _resolve_area_info_assertion)

class MapMethods:
    _GHKEY_SCRATCH = GHKey()

    @staticmethod
    def GetMapInfo(map_id: int):
        """Return AreaInfoStruct for any map_id (not just the current map)."""
        from ..context.InstanceInfoContext import AreaInfoStruct
        if map_id <= 0 or not _area_info_addr:
            return None

        base = ctypes.cast(_area_info_addr, ctypes.POINTER(ctypes.c_uint32)).contents.value
        if not base:
            return None

        target_addr = base + (map_id * ctypes.sizeof(AreaInfoStruct))
        try:
            return ctypes.cast(target_addr, ctypes.POINTER(AreaInfoStruct)).contents
        except (ValueError, OSError):
            return None

    @staticmethod
    def SkipCinematic() -> bool:
        """Skip the current map cinematic."""
        if SkipCinematic_Func is None or not SkipCinematic_Func.is_valid():
            return False

        SkipCinematic_Func()
        return True

    @staticmethod
    def Travel(map_id: int, region: int = 0, district_number: int = 0, language: int = 0) -> bool:
        class TravelStruct(ctypes.Structure):
            _fields_ = [
                ("map_id", ctypes.c_uint32),  # GW::Constants::MapID
                ("region", ctypes.c_int32),  # ServerRegion
                ("language", ctypes.c_int32),  # Language
                ("district_number", ctypes.c_int32),
            ]

        return UIManager.SendUIMessageRaw(
            UIMessage.kTravel,
            ctypes.addressof(TravelStruct(map_id=map_id, region=region, language=language, district_number=district_number)),
            False,
        )

    @staticmethod
    def TravelGH(key: GHKey | None = None) -> bool:
        """
        Travel to a Guild Hall.
        If a key is provided, its value is written into the existing
        player_gh_key before sending the UI message.
        """
        guild_ctx = GuildContext.get_context()
        if guild_ctx is None:
            return False

        gh_key = guild_ctx.player_gh_key
        if gh_key is None:
            return False

        # If a custom key was provided, stuff its value into the real GH key
        if key is not None:
            for i in range(4):
                gh_key.key_data[i] = key.key_data[i]

        # Always use the original, working pointer
        return UIManager.SendUIMessageRaw(
            UIMessage.kGuildHall,
            ctypes.addressof(gh_key),
            0,
            False
        )

    @staticmethod
    def LeaveGH() -> bool:
        """Leave the current Guild Hall."""
        return UIManager.SendUIMessage(
            UIMessage.kLeaveGuildHall,
            [0],
            False
        )
        
    @staticmethod
    def EnterChallenge() -> bool:
        """Enter the challenge mode from the Guild Hall."""
        return UIManager.SendUIMessage(
            UIMessage.kSendEnterMission,
            [0],
            False
        )

    @staticmethod
    def LogouttoCharacterSelect() -> None:
        def _action():
            UIManager.SendUIMessage(UIMessage.kLogout,[0,0])
        
        Game.enqueue(_action)
        
