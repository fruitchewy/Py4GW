from ...Scanner import ScannerSection
from ..internals.prototypes import Prototypes
from ..internals.native_function import NativeFunction
from ..internals.scan_resolver import resolve_scan

from ...enums_src.UI_enums import UIMessage
from ...Scanner import Scanner
import ctypes
from typing import List, Optional
from Py4GW import Game

class WorldActionId:
    InteractEnemy = 0
    InteractPlayerOrOther = 1
    InteractNPC = 2
    InteractItem = 3
    InteractTrade = 4
    InteractGadget = 5


# --- MoveTo ---
# No viable assertion anchor — the AgApi.cpp wrapper doesn't call MoveTo directly.
# Pattern-only for now.
def _resolve_moveto_pattern() -> int:
    addr = Scanner.Find(
        b"\x83\xc4\x0c\x85\xff\x74\x0b\x56\x6a\x03", "xxxxxxxxxx",
        -0x5, ScannerSection.TEXT)
    if not addr:
        return 0
    return Scanner.FunctionFromNearCall(addr, True)

_moveto_addr = resolve_scan("MoveTo", _resolve_moveto_pattern)
MoveTo_Func: Optional[NativeFunction] = (
    NativeFunction.from_address(
        name="MoveTo_Func",
        address=_moveto_addr,
        prototype=Prototypes["Void_FloatPtr"],
    ) if _moveto_addr else None
)


# --- DepositFaction ---
# Primary: unique assertion in the deposit event handler
# Fallback: legacy byte pattern
def _resolve_deposit_assertion() -> int:
    assertion = Scanner.FindAssertion(
        "VnGuildAdjustFaction.cpp", "msg.notifyParam", 0, 0)
    if not assertion:
        return 0
    handler_fn = Scanner.ToFunctionStart(assertion, 0xFFF)
    if not handler_fn:
        return 0
    # Within the handler, find push 0x1388 (5000) — the deposit amount constant.
    # The CALL to the deposit function follows at +0xA from the push.
    push_5000 = Scanner.FindInRange(
        b"\x68\x88\x13\x00\x00", "xxxxx", 0,
        handler_fn, handler_fn + 0x200)
    if not push_5000:
        return 0
    return push_5000 + 0xA

_deposit_addr = resolve_scan("DepositFaction", _resolve_deposit_assertion)
DepositFaction_Func: Optional[NativeFunction] = (
    NativeFunction.from_address(
        name="DepositFaction_Func",
        address=_deposit_addr,
        prototype=Prototypes["Void_U32_U32_U32"],
    ) if _deposit_addr else None
)


# --- SetActiveTitle ---
# Already assertion-based (HIGH resilience)
_sat_addr = resolve_scan("SetActiveTitle", lambda: (
    Scanner.FunctionFromNearCall(
        Scanner.FindInRange(
            b"\xff\x76\x08\xe8", "xxxx", 3,
            Scanner.ToFunctionStart(
                Scanner.FindAssertion("AttribTitles.cpp", "!*hdr.param", 0, 0)),
            Scanner.ToFunctionStart(
                Scanner.FindAssertion("AttribTitles.cpp", "!*hdr.param", 0, 0)) + 0x3FF))
))
SetActiveTitle_Func: Optional[NativeFunction] = (
    NativeFunction.from_address(
        name="SetActiveTitle_Func",
        address=_sat_addr,
        prototype=Prototypes["Void_U32"],
    ) if _sat_addr else None
)


# --- RemoveActiveTitle ---
# Relative to SetActiveTitle (MEDIUM resilience — depends on SetActiveTitle)
_rat_addr = resolve_scan("RemoveActiveTitle", lambda: (
    Scanner.FindInRange(
        b"\x55\x8b\xec\x51", "xxxx", 0,
        _sat_addr + 0x10, _sat_addr + 0xFF)
    if _sat_addr else 0
))
RemoveActiveTitle_Func: Optional[NativeFunction] = (
    NativeFunction.from_address(
        name="RemoveActiveTitle_Func",
        address=_rat_addr,
        prototype=Prototypes["Void_NoArgs"],
    ) if _rat_addr else None
)
    
            
class PlayerMethods:
    @staticmethod
    def ChangeTarget(agent_id: int) -> None:
        def _action():
            from ...Agent import Agent
            from ...UIManager import UIManager
            if (target := Agent.GetAgentByID(agent_id)) is None:
                return 
            UIManager.SendUIMessage(UIMessage.kSendChangeTarget,[target.agent_id])
        
        Game.enqueue(_action)
        
    @staticmethod
    def InteractAgent(agent_id: int, call_target: bool = False) -> None:
        def _action():
            from ...Agent import Agent
            from ...UIManager import UIManager
            
            if (agent := Agent.GetAgentByID(agent_id)) is None:
                return 

            # Default packet values
            action_id = WorldActionId.InteractEnemy

            if agent.is_item_type:
                action_id = WorldActionId.InteractItem

            elif agent.is_gadget_type:
                action_id = WorldActionId.InteractGadget

            else:
                if (living := agent.GetAsAgentLiving()) is None:
                    return
                
                """ 1: "ally",
                2: "neutral",
                3: "enemy",
                4: "spirit_pet",
                5: "minion",
                6: "npc_minipet","""

                if living.allegiance == 3:  # Enemy
                    action_id = WorldActionId.InteractEnemy
                elif living.allegiance == 6:  # Npc_Minipet
                    action_id = WorldActionId.InteractNPC
                else:
                    action_id = WorldActionId.InteractPlayerOrOther

            UIManager.SendUIMessage(
                UIMessage.kSendWorldAction,
                [action_id, agent_id, call_target]
            )

        Game.enqueue(_action)
        
    @staticmethod
    def Move(x: float, y: float, zPlane: int = 0) -> None:
        def _action():
            if MoveTo_Func is None or not MoveTo_Func.is_valid():
                return

            args = (ctypes.c_float * 4)()
            args[0] = x
            args[1] = y
            args[2] = float(zPlane)
            args[3] = 0.0

            MoveTo_Func.directCall(args)

        Game.enqueue(_action)

    @staticmethod
    def DepositFaction(allegiance: int) -> None:
        def _action():
            if DepositFaction_Func is None or not DepositFaction_Func.is_valid():
                return
            DepositFaction_Func.directCall(0, allegiance, 5000)

        Game.enqueue(_action)

    @staticmethod
    def SetActiveTitle(title_id: int) -> None:
        def _action():
            if SetActiveTitle_Func is None or not SetActiveTitle_Func.is_valid():
                return
            SetActiveTitle_Func.directCall(title_id)

        Game.enqueue(_action)

    @staticmethod
    def RemoveActiveTitle() -> None:
        def _action():
            if RemoveActiveTitle_Func is None or not RemoveActiveTitle_Func.is_valid():
                return
            RemoveActiveTitle_Func.directCall()

        Game.enqueue(_action)
        
    @staticmethod
    def SendChat(channel: int | str, message: str) -> bool:
        """
        1:1 parity with:
        bool Chat::SendChat(char channel, const char* msg)
        -> bool Chat::SendChat(char channel, const wchar_t* msg)
        -> SendChat_Func(buffer, 0)  (hook turns into UIMessage)
        """
        if not message:
            return False

        if isinstance(channel, str):
            if len(channel) != 1:
                return False
            ch = channel
        elif isinstance(channel, int):
            if not (0 <= channel <= 0xFF):
                return False
            ch = chr(channel)
        else:
            return False

        # Match GetChannel(channel) != CHANNEL_UNKNOW
        if ch not in ('!', '@', '#', '$', '%', '"','/'):
            return False

        # Mimic char* -> wchar_t* overload path
        try:
            msg_w = message.encode("utf-8").decode("mbcs", errors="replace")
        except Exception:
            return False

        if not msg_w or len(msg_w) >= 140:
            return False

        # Clamp to in-game limit
        msg_w = msg_w[:120]

        # ---------- ASYNC EXECUTION ----------

        def _do_action():
            Buffer140 = ctypes.c_wchar * 140
            buf = Buffer140()

            buf[0] = ch
            for i, c in enumerate(msg_w):
                buf[i + 1] = c
            buf[len(msg_w) + 1] = "\0"

            class SendChatPacket(ctypes.Structure):
                _fields_ = [
                    ("message", ctypes.c_wchar_p),
                    ("agent_id", ctypes.c_uint32),
                ]

            packet = SendChatPacket(
                message=ctypes.cast(buf, ctypes.c_wchar_p),
                agent_id=0,
            )

            from ...UIManager import UIManager
            UIManager.SendUIMessageRaw(
                UIMessage.kSendChatMessage,
                ctypes.addressof(packet),
                False,
            )

        Game.enqueue(_do_action)

        return True
    
    @staticmethod
    def SendWhisper(name: str, message: str) -> bool:
        """
        bool Chat::SendChat(const char* from, const char* msg)
        -> swprintf(L"\"%S,%S", from, msg)
        -> SendChat_Func(buffer, 0)
        """
        if not name or not message:
            return False

        # ---- Mimic char* -> wchar_t* via %S (ACP / mbcs) ----
        try:
            from_w = name.encode("utf-8").decode("mbcs", errors="replace")
            msg_w  = message.encode("utf-8").decode("mbcs", errors="replace")
        except Exception:
            return False

        if not from_w or not msg_w:
            return False

        # ---- swprintf(L"\"%S,%S", from, msg) ----
        formatted = f"\"{from_w},{msg_w}"

        if not (0 < len(formatted) < 140):
            return False

        # ---------- ASYNC EXECUTION ----------
        def _do_action():
            Buffer140 = ctypes.c_wchar * 140
            buf = Buffer140()

            for i, c in enumerate(formatted):
                buf[i] = c
            buf[len(formatted)] = "\0"

            class SendChatPacket(ctypes.Structure):
                _fields_ = [
                    ("message", ctypes.c_wchar_p),
                    ("agent_id", ctypes.c_uint32),
                ]

            packet = SendChatPacket(
                message=ctypes.cast(buf, ctypes.c_wchar_p),
                agent_id=0,
            )

            from ...UIManager import UIManager
            UIManager.SendUIMessageRaw(
                UIMessage.kSendChatMessage,
                ctypes.addressof(packet),
                False,
            )

        Game.enqueue(_do_action)
        return True

    @staticmethod
    def SendChatCommand(message: str) -> bool:
        """
        void PyPlayer::SendChatCommand(std::string msg)
        -> Chat::SendChat('/', msg.c_str())
        """
        return PlayerMethods.SendChat('/', message)

    @staticmethod
    def SendFakeChat(channel: int, message: str) -> None:
        """
        1:1 parity with:
        PyPlayer::SendFakeChat
        -> Chat::SendFakeChat
        -> WriteChat
        -> WriteChatEnc
        """

        # -----------------------------
        # C++: std::wstring(message.begin(), message.end())
        # widen each UTF-8 byte to wchar
        # -----------------------------
        msg_bytes = message.encode("utf-8")
        wmessage = "".join(chr(b) for b in msg_bytes)

        def _do_action():
            # -----------------------------
            # WriteChat
            # swprintf(L"\x108\x107%s\x1", message)
            # -----------------------------
            message_encoded = f"\u0108\u0107{wmessage}\u0001"

            sender_encoded = None  # SendFakeChat never supplies sender
            final_message = message_encoded

            # -----------------------------
            # WriteChatEnc
            # -----------------------------
            if sender_encoded is not None:
                has_link = "<a=1>" in message_encoded
                has_markup = has_link or "<c=" in message_encoded

                if has_markup:
                    if has_link:
                        fmt = (
                            "\u0108\u0107<a=2>\u0001\u0002%s\u0002"
                            "\u0108\u0107</a>\u0001\u0002"
                            "\u0108\u0107: \u0001\u0002%s"
                        )
                    else:
                        fmt = (
                            "\u0108\u0107<a=1>\u0001\u0002%s\u0002"
                            "\u0108\u0107</a>\u0001\u0002"
                            "\u0108\u0107: \u0001\u0002%s"
                        )
                else:
                    fmt = "\u076b\u010a%s\u0001\u010b%s\u0001"

                final_message = fmt % (sender_encoded, message_encoded)

            # -----------------------------
            # UIChatMessage
            # -----------------------------
            class UIChatMessage(ctypes.Structure):
                _fields_ = [
                    ("channel", ctypes.c_uint32),
                    ("message", ctypes.c_wchar_p),
                    ("channel2", ctypes.c_uint32),
                ]


            param = UIChatMessage(
                channel=channel,
                message=final_message,
                channel2=channel,
            )

            from ...UIManager import UIManager
            UIManager.SendUIMessageRaw(
                UIMessage.kWriteToChatLog,
                ctypes.addressof(param),
                False,
            )

        Game.enqueue(_do_action)

    @staticmethod
    def SendRawDialog(dialog_id: int) -> None:
        """
        Send a dialog using kSendAgentDialog.
        Works for skill trainers, NPC dialogs, and merchant tabs.
        """

        def _action():
            from ...UIManager import UIManager

            UIManager.SendUIMessageRaw(UIMessage.kSendAgentDialog, dialog_id, 0)

        Game.enqueue(_action)

    @staticmethod
    def SendSkillTrainerDialog(skill_id: int) -> None:
        """
        Buy/Learn a skill from a Skill Trainer.

        Args:
            skill_id: The skill ID to purchase
        """
        from ...py4gwcorelib_src.Utils import Utils
        dialog_skill_id = Utils.SkillIdToDialogId(skill_id)
        PlayerMethods.SendRawDialog(dialog_skill_id)
