from dataclasses import dataclass,field
from enum import Enum
class TaskStatus(str,Enum):
    PENDING="pending";RUNNING="running";COMPLETED="completed";FAILED="failed";MAX_ITERATIONS="max_iterations"
@dataclass
class AgentEvent:
    iteration:int;event_type:str;message:str;data:dict=field(default_factory=dict)
@dataclass
class TaskState:
    task:str;project_root:str;status:TaskStatus=TaskStatus.PENDING;iteration:int=0;events:list[AgentEvent]=field(default_factory=list)
    def record(self,event_type:str,message:str,**data)->None:self.events.append(AgentEvent(self.iteration,event_type,message,data))
