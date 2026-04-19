from typing import List, Optional
from ..internals.types import CPointer
from ..internals.gw_array import GW_Array
from ..internals.types import Vec3f

class AgentSummaryInfoSub():
    h0000: int
    h0004: int
    gadget_id: int
    h000C: int
    gadget_name_enc: CPointer[str]
    h0014: int
    composite_agent_id: int

    @property
    def gadget_name_encoded_str(self) -> str | None:...
    @property
    def gadget_name_str(self) -> str | None:...
    
class AgentSummaryInfo():
    h0000: int
    h0004: int
    extra_info_sub_ptr: CPointer[AgentSummaryInfoSub]
    @property
    def extra_info_sub(self) -> Optional[AgentSummaryInfoSub]:...
    
   
class AgentMovement():
    h0000: List[int]
    move_state: int              # +0x0008  Movement phase (idle/moving/pathfinding transitions)
    agent_id: int
    h0010: List[int]
    agentDef: int
    h0020: List[int]
    moving1: int
    h003C: List[int]
    moving2: int
    h0048: List[int]
    h005C: int
    speed_modifier: float        # +0x0060  Movement speed multiplier. Increases sharply on aggro transition.
    position: Vec3f              # +0x0064  (0, world_x, world_y)
    h0070: int
    position2: Vec3f             # +0x0074  position copy
    h0080: List[int]
    dest_x: float                # +0x0088  Movement destination X (inf = stopped)
    dest_y: float                # +0x008C  Movement destination Y (inf = stopped)
    h0090: int
    h0094: int
    movement_target_id: int      # +0x0098  Melee aggro target (0 = none). Casters may cast without setting this.
    dest_x2: float               # +0x009C  Cached destination X
    dest_y2: float               # +0x00A0  Cached destination Y
    h00A4: List[int]
    dir_offset_x: float          # +0x00B0  Direction vector to destination
    dir_offset_y: float          # +0x00B4
    h00B8: int
    heading_sin: float           # +0x00BC  sin(movement heading)
    heading_cos: float           # +0x00C0  cos(movement heading)
    
class AccAgentContextStruct():
    h0000_array: GW_Array
    h0010: List[int]
    h0024: int
    h0028: List[int]
    h0030: int
    h0034: List[int]
    h003C: int
    h0040: List[int]
    h0048: int
    h004C: List[int]
    h0054: int
    h0058: List[int]
    h0084_array: GW_Array
    h0094: int
    agent_summary_info_array: GW_Array
    h00A8_array: GW_Array
    h00B8_array: GW_Array
    rand1: int
    rand2: int
    h00D0: List[int]
    agent_movement_array: GW_Array
    h00F8_array: GW_Array
    h0108: List[int]
    h014C_array: GW_Array
    h015C_array: GW_Array
    h016C: List[int]
    instance_timer: int

    @property
    def h0000_ptrs(self) -> list[int] | None:...
    @property
    def h0084_ptrs(self) -> list[int] | None:...
    @property
    def agent_summary_info_list(self) -> list[AgentSummaryInfo] | None:...
    @property
    def h00A8_ptrs(self) -> list[int] | None:...
    @property
    def h00B8_ptrs(self) -> list[int] | None:...
    @property
    def agent_movement_ptrs(self) -> list[AgentMovement] | None:...
    @property
    def valid_agents_ids(self) -> list[int]:...
    @property
    def h00F8_ptrs(self) -> list[int] | None:...
    @property
    def h014C_ptrs(self) -> list[int] | None:...
    @property
    def h015C_ptrs(self) -> list[int] | None:...
    
    
    
class AccAgentContext:
    @staticmethod
    def get_ptr() -> int:... 
    @staticmethod
    def _update_ptr():...
    @staticmethod
    def enable():...
    @staticmethod
    def disable():...
    @staticmethod
    def get_context() -> AccAgentContextStruct | None:...
        