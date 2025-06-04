"""
Cloud-specific events for remote synchronization with the backend.
Each event represents a CREATE operation for the corresponding database model.
"""

from datetime import datetime
from typing import Optional, Any
import base64
import asyncio

from pydantic import BaseModel, Field, field_validator, model_validator, PrivateAttr, field_serializer
from uuid_extensions import uuid7str


# Constants for validation
MAX_STRING_LENGTH = 10000  # 10K chars for most strings
MAX_URL_LENGTH = 2000
MAX_TASK_LENGTH = 5000
MAX_COMMENT_LENGTH = 2000
MAX_FILE_CONTENT_SIZE = 50 * 1024 * 1024  # 50MB

# Base event model
class BaseEvent(BaseModel):
    event_type: str
    event_id: str = Field(default_factory=uuid7str)
    event_at: datetime = Field(default_factory=datetime.utcnow)
    event_schema: Optional[str] = Field(default=None, description="Event schema version in format ClassName@version", max_length=100)
    event_path: list[str] = Field(default_factory=list, description="Path tracking for event routing")
    
    # Completion tracking fields (excluded from serialization to backend)
    started_at: Optional[datetime] = Field(default=None, exclude=True)
    completed_at: Optional[datetime] = Field(default=None, exclude=True)
    results: dict[str, Any] = Field(default_factory=dict, exclude=True)
    errors: dict[str, str] = Field(default_factory=dict, exclude=True)  # Store error messages as strings
    
    # Private field for completion tracking
    _completion_event: Optional[asyncio.Event] = PrivateAttr(default=None)
    
    @model_validator(mode='before')
    @classmethod
    def truncate_strings(cls, values):
        """Truncate string fields to reasonable lengths."""
        if isinstance(values, dict):
            for field_name, value in values.items():
                if isinstance(value, str) and field_name not in ['event_id', 'event_type', 'event_schema']:
                    # Get field info from model
                    if field_name in cls.model_fields:
                        field_info = cls.model_fields[field_name]
                        max_length = getattr(field_info, 'max_length', MAX_STRING_LENGTH)
                        if len(value) > max_length:
                            values[field_name] = value[:max_length]
        return values
    
    def model_post_init(self, __context: Any) -> None:
        """Initialize completion event after model creation"""
        try:
            # Only create event if we're in an async context
            asyncio.get_running_loop()
            self._completion_event = asyncio.Event()
        except RuntimeError:
            # Not in async context, skip
            self._completion_event = None
    
    async def wait_for_completion(self) -> None:
        """Wait for this event to be fully processed"""
        if self._completion_event:
            await self._completion_event.wait()
    
    def mark_completed(self) -> None:
        """Mark this event as completed"""
        self.completed_at = datetime.utcnow()
        if self._completion_event:
            self._completion_event.set()
    
    def model_dump_with_metadata(self, **kwargs) -> dict[str, Any]:
        """Dump the model including metadata fields that are normally excluded"""
        # Get the base dump without excluded fields
        data = self.model_dump(**kwargs)
        
        # Manually add the excluded fields if they have values
        if self.started_at:
            data['started_at'] = self.started_at
        if self.completed_at:
            data['completed_at'] = self.completed_at
        if self.results:
            data['results'] = self.results
        if self.errors:
            data['errors'] = self.errors
            
        return data


# AgentSessionModel events
class CreateAgentSessionEvent(BaseEvent):
    event_type: str = Field(default="CreateAgentSession", frozen=True)
    
    # Model fields
    id: str = Field(default_factory=uuid7str)
    user_id: str = Field(max_length=255)
    browser_session_id: str = Field(max_length=255)
    browser_session_live_url: str = Field(max_length=MAX_URL_LENGTH)
    browser_session_cdp_url: str = Field(max_length=MAX_URL_LENGTH)
    browser_session_stopped: bool = False
    browser_session_stopped_at: Optional[datetime] = None
    is_source_api: Optional[bool] = None
    browser_state: dict = Field(default_factory=dict)
    browser_session_data: Optional[dict] = None


# AgentTaskModel events
class CreateAgentTaskEvent(BaseEvent):
    event_type: str = Field(default="CreateAgentTask", frozen=True)
    
    # Model fields
    id: str = Field(default_factory=uuid7str)
    user_id: str = Field(max_length=255)  # Added for authorization checks
    agent_session_id: str
    llm_model: str = Field(max_length=100)  # LLMModel enum value as string
    stopped: bool = False
    paused: bool = False
    task: str = Field(max_length=MAX_TASK_LENGTH)
    done_output: Optional[str] = Field(None, max_length=MAX_STRING_LENGTH)
    scheduled_task_id: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    agent_state: dict = Field(default_factory=dict)
    user_feedback_type: Optional[str] = Field(None, max_length=10)  # UserFeedbackType enum value as string
    user_comment: Optional[str] = Field(None, max_length=MAX_COMMENT_LENGTH)
    gif_url: Optional[str] = Field(None, max_length=MAX_URL_LENGTH)


# AgentStepModel events
class CreateAgentStepEvent(BaseEvent):
    event_type: str = Field(default="CreateAgentStep", frozen=True)
    
    # Model fields
    id: UUID = Field(default_factory=uuid7str)
    user_id: str = Field(max_length=255)  # Added for authorization checks
    created_at: datetime = Field(default_factory=datetime.utcnow)
    agent_task_id: str
    step: int
    evaluation_previous_goal: str = Field(max_length=MAX_STRING_LENGTH)
    memory: str = Field(max_length=MAX_STRING_LENGTH)
    next_goal: str = Field(max_length=MAX_STRING_LENGTH)
    actions: list[dict]
    screenshot_url: Optional[str] = Field(None, max_length=MAX_URL_LENGTH)
    url: str = Field(default='', max_length=MAX_URL_LENGTH)
    
    @field_validator('screenshot_url')
    @classmethod
    def validate_screenshot_size(cls, v: Optional[str]) -> Optional[str]:
        """Validate screenshot URL or base64 content size."""
        if v is None or not v.startswith('data:'):
            return v
        # It's base64 data, check size
        if ',' in v:
            base64_part = v.split(',')[1]
            estimated_size = len(base64_part) * 3 / 4
            if estimated_size > MAX_FILE_CONTENT_SIZE:
                raise ValueError(f"Screenshot content exceeds maximum size of {MAX_FILE_CONTENT_SIZE / 1024 / 1024}MB")
        return v


# UserUploadedFileModel events
class CreateUserUploadedFileEvent(BaseEvent):
    event_type: str = Field(default="CreateUserUploadedFile", frozen=True)
    
    # Model fields
    id: UUID = Field(default_factory=uuid7str)
    user_id: str = Field(max_length=255)
    task_id: str
    file_name: str = Field(max_length=255)
    file_content: Optional[str] = None  # Base64 encoded file content
    content_type: Optional[str] = Field(None, max_length=100)  # MIME type for file uploads
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    @field_validator('file_content')
    @classmethod
    def validate_file_size(cls, v: Optional[str]) -> Optional[str]:
        """Validate base64 file content size."""
        if v is None:
            return v
        # Remove data URL prefix if present
        if ',' in v:
            v = v.split(',')[1]
        # Estimate decoded size (base64 is ~33% larger)
        estimated_size = len(v) * 3 / 4
        if estimated_size > MAX_FILE_CONTENT_SIZE:
            raise ValueError(f"File content exceeds maximum size of {MAX_FILE_CONTENT_SIZE / 1024 / 1024}MB")
        return v


# AgentOutputFileModel events
class CreateAgentOutputFileEvent(BaseEvent):
    event_type: str = Field(default="CreateAgentOutputFile", frozen=True)
    
    # Model fields
    id: UUID = Field(default_factory=uuid7str)
    user_id: str = Field(max_length=255)
    task_id: str
    file_name: str = Field(max_length=255)
    file_content: Optional[str] = None  # Base64 encoded file content
    content_type: Optional[str] = Field(None, max_length=100)  # MIME type for file uploads
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    @field_validator('file_content')
    @classmethod
    def validate_file_size(cls, v: Optional[str]) -> Optional[str]:
        """Validate base64 file content size."""
        if v is None:
            return v
        # Remove data URL prefix if present
        if ',' in v:
            v = v.split(',')[1]
        # Estimate decoded size (base64 is ~33% larger)
        estimated_size = len(v) * 3 / 4
        if estimated_size > MAX_FILE_CONTENT_SIZE:
            raise ValueError(f"File content exceeds maximum size of {MAX_FILE_CONTENT_SIZE / 1024 / 1024}MB")
        return v


# UserBrowserProfileModels events
class CreateUserBrowserProfileEvent(BaseEvent):
    event_type: str = Field(default="CreateUserBrowserProfile", frozen=True)
    
    # Model fields
    id: str = Field(default_factory=lambda: str(UUID()))
    user_id: str
    profile_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Union type for all events
from typing import Union

EventType = Union[
    CreateAgentSessionEvent,
    CreateAgentTaskEvent,
    CreateAgentStepEvent,
    CreateUserUploadedFileEvent,
    CreateAgentOutputFileEvent,
    CreateUserBrowserProfileEvent,
]


# Event type mapping
EVENT_TYPE_MAP = {
    "CreateAgentSession": CreateAgentSessionEvent,
    "CreateAgentTask": CreateAgentTaskEvent,
    "CreateAgentStep": CreateAgentStepEvent,
    "CreateUserUploadedFile": CreateUserUploadedFileEvent,
    "CreateAgentOutputFile": CreateAgentOutputFileEvent,
    "CreateUserBrowserProfile": CreateUserBrowserProfileEvent,
}