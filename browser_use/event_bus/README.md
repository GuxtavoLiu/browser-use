# Browser-Use Event Bus

The event bus is a powerful async messaging system that enables decoupled communication between browser-use components and external services. It provides a foundation for cloud integration, remote browser sessions, telemetry, and extensibility.

## Table of Contents

- [Overview](#overview)
- [Core Concepts](#core-concepts)
- [Basic Usage](#basic-usage)
- [Event Types](#event-types)
- [Remote Browser Sessions](#remote-browser-sessions)
- [Remote LLM Integration](#remote-llm-integration)
- [Cloud Integration](#cloud-integration)
- [Advanced Patterns](#advanced-patterns)
- [Best Practices](#best-practices)

## Overview

The event bus implements an async, FIFO event processing system with:

- **Write-ahead logging** - All events are logged before processing
- **Parallel handler execution** - Multiple handlers run concurrently per event
- **Serial event processing** - Events are processed in order
- **Completion tracking** - Wait for events to be fully processed
- **Error resilience** - Failed handlers don't crash the system
- **Flexible subscription** - Subscribe by event type, model class, or all events

## Core Concepts

### Events

Events are Pydantic models that inherit from `BaseEvent`:

```python
from browser_use.event_bus import BaseEvent
from pydantic import Field

class MyCustomEvent(BaseEvent):
    event_type: str = Field(default="MyCustomEvent", frozen=True)
    user_id: str
    action: str
    metadata: dict = Field(default_factory=dict)
```

### Event Bus

The `EventBus` processes events asynchronously:

```python
from browser_use.event_bus import EventBus

# Create and start event bus
event_bus = EventBus(name="my_bus")
await event_bus.start()

# Subscribe handlers
async def my_handler(event: MyCustomEvent):
    print(f"User {event.user_id} performed {event.action}")
    return {"processed": True}

event_bus.on(MyCustomEvent, my_handler)

# Emit events
event = MyCustomEvent(user_id="123", action="click")
await event_bus.enqueue(event)

# Wait for processing
await event_bus.wait_for_empty_queue()
```

### Handler Types

Handlers can be sync or async functions:

```python
# Async handler
async def async_handler(event: BaseEvent):
    await some_async_operation()
    return "processed"

# Sync handler
def sync_handler(event: BaseEvent):
    do_something()
    return "processed"

# Subscribe both
event_bus.on("*", async_handler)  # All events
event_bus.on("SpecificEvent", sync_handler)  # Specific type
```

## Basic Usage

### With Browser-Use Agent

The agent automatically uses an event bus for lifecycle events:

```python
from browser_use import Agent

agent = Agent(task="Navigate to example.com")

# Access the agent's event bus
agent.event_bus.on("TaskCompletedEvent", my_completion_handler)

await agent.run()
```

### Standalone Event Bus

```python
# Create event bus
bus = EventBus()
await bus.start()

# Process events
event = await bus.enqueue_and_wait(MyEvent(data="test"))

# Check results
print(event.results)  # {"handler_name": result, ...}
print(event.errors)   # {"failed_handler": "error message"}

await bus.stop()
```

## Event Types

### Agent Lifecycle Events

```python
from browser_use.event_bus import (
    SessionStartedEvent,    # Agent session begins
    SessionStoppedEvent,    # Agent session ends
    TaskStartedEvent,       # Task execution begins
    TaskCompletedEvent,     # Task finishes successfully
    TaskFailedEvent,        # Task encounters error
    TaskPausedEvent,        # Task is paused
    TaskResumedEvent,       # Task is resumed
    StepCreatedEvent,       # Agent takes an action
)
```

### Cloud Sync Events

```python
from browser_use.event_bus import (
    CreateAgentSessionEvent,      # Sync session to backend
    CreateAgentTaskEvent,         # Sync task to backend
    CreateAgentStepEvent,         # Sync step to backend
    CreateUserUploadedFileEvent,  # Track file uploads
    CreateAgentOutputFileEvent,   # Track generated files
)
```

## Remote Browser Sessions

The event bus enables requesting browser sessions from a remote backend instead of launching local browsers.

### Architecture

```
┌─────────────┐         ┌──────────┐         ┌─────────────┐
│ BrowserSession │ ──────> │ Event Bus │ ──────> │   Backend   │
│             │         │          │         │             │
│ 1. Request  │         │ 2. Route │         │ 3. Allocate │
│    Browser  │         │   Event  │         │    Browser  │
│             │         │          │         │             │
│ 6. Connect │ <────── │ 5. Route │ <────── │ 4. Response │
│    to CDP   │         │ Response │         │    Event    │
└─────────────┘         └──────────┘         └─────────────┘
```

### Event Definitions

```python
from browser_use.event_bus import BaseEvent
from datetime import datetime
from pydantic import Field
from uuid_extensions import uuid7str

class RequestBrowserSessionEvent(BaseEvent):
    """Request a remote browser session from backend"""
    event_type: str = Field(default="RequestBrowserSession", frozen=True)
    
    # Request details
    request_id: str = Field(default_factory=uuid7str)
    user_id: str
    browser_profile: dict  # Serialized BrowserProfile
    timeout_seconds: int = Field(default=60)
    
    # Optional preferences
    preferred_region: str | None = None
    preferred_browser_type: str | None = None
    session_tags: dict[str, str] = Field(default_factory=dict)


class BrowserSessionReadyEvent(BaseEvent):
    """Backend response with browser connection details"""
    event_type: str = Field(default="BrowserSessionReady", frozen=True)
    
    # Correlation
    request_id: str  # Matches RequestBrowserSessionEvent.request_id
    
    # Connection details
    cdp_url: str  # Chrome DevTools Protocol URL
    wss_url: str | None = None  # WebSocket URL (optional)
    browser_session_id: str
    browser_session_live_url: str | None = None
    
    # Metadata
    expires_at: datetime
    region: str
    browser_type: str
    session_data: dict = Field(default_factory=dict)


class BrowserSessionErrorEvent(BaseEvent):
    """Error response when browser cannot be allocated"""
    event_type: str = Field(default="BrowserSessionError", frozen=True)
    
    request_id: str
    error_code: str
    error_message: str
    retry_after_seconds: int | None = None
```

### Client Implementation

Integrate the event bus into BrowserSession to request remote browsers:

```python
class BrowserSession(BaseModel):
    # Add event bus field
    event_bus: EventBus | None = None
    
    async def setup_browser_via_event_bus(self) -> None:
        """Request remote browser from backend"""
        if not self.event_bus or not self.browser_profile.use_remote_browser:
            return
            
        # Create request
        request = RequestBrowserSessionEvent(
            user_id=self.browser_profile.user_id,
            browser_profile=self.browser_profile.model_dump(),
            preferred_region=self.browser_profile.preferred_region,
        )
        
        # Set up response handler
        response_future = asyncio.Future()
        
        async def handle_response(event):
            if event.request_id == request.request_id:
                if isinstance(event, BrowserSessionReadyEvent):
                    response_future.set_result(event)
                else:  # Error event
                    response_future.set_exception(
                        RuntimeError(f"Browser allocation failed: {event.error_message}")
                    )
        
        # Subscribe to responses
        self.event_bus.on(BrowserSessionReadyEvent, handle_response)
        self.event_bus.on(BrowserSessionErrorEvent, handle_response)
        
        # Send request and wait
        await self.event_bus.enqueue(request)
        response = await asyncio.wait_for(
            response_future, 
            timeout=request.timeout_seconds
        )
        
        # Use remote browser details
        self.cdp_url = response.cdp_url
        self.wss_url = response.wss_url
        self.browser_session_id = response.browser_session_id
```

### Backend Handler

Implement a backend service that allocates browsers:

```python
class RemoteBrowserBackend:
    def __init__(self, event_bus: EventBus, browser_pool: BrowserPool):
        self.event_bus = event_bus
        self.browser_pool = browser_pool
        
        # Subscribe to requests
        event_bus.on(RequestBrowserSessionEvent, self.handle_request)
    
    async def handle_request(self, event: RequestBrowserSessionEvent):
        """Allocate browser and send response"""
        try:
            # Get browser from pool
            browser = await self.browser_pool.allocate(
                user_id=event.user_id,
                profile=event.browser_profile,
                region=event.preferred_region,
            )
            
            # Send success response
            response = BrowserSessionReadyEvent(
                request_id=event.request_id,
                cdp_url=browser.cdp_url,
                wss_url=browser.wss_url,
                browser_session_id=browser.session_id,
                expires_at=browser.expires_at,
                region=browser.region,
                browser_type=browser.browser_type,
            )
            
            await self.event_bus.enqueue(response)
            
        except Exception as e:
            # Send error response
            error = BrowserSessionErrorEvent(
                request_id=event.request_id,
                error_code="ALLOCATION_FAILED",
                error_message=str(e),
                retry_after_seconds=30,
            )
            
            await self.event_bus.enqueue(error)
```

### Complete Example

```python
import asyncio
from browser_use import Agent, BrowserSession
from browser_use.event_bus import EventBus

async def main():
    # Create shared event bus
    event_bus = EventBus(name="remote_browser_bus")
    await event_bus.start()
    
    # Set up backend handler (in production, runs on server)
    backend = RemoteBrowserBackend(event_bus, browser_pool)
    
    # Create browser session with event bus
    browser_session = BrowserSession(
        event_bus=event_bus,
        use_remote_browser=True,
        user_id="user_123",
        preferred_region="us-east-1",
    )
    
    # Start will request remote browser via event bus
    await browser_session.start()
    
    # Use with agent
    agent = Agent(
        task="Navigate to example.com",
        browser_session=browser_session,
    )
    
    await agent.run()
    await browser_session.stop()
    await event_bus.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Remote LLM Integration

The event bus enables abstracting LLM calls to a unified backend, allowing centralized model management, rate limiting, caching, and usage tracking.

### Architecture

```
┌─────────────┐         ┌──────────┐         ┌─────────────┐
│    Agent    │ ──────> │ Event Bus │ ──────> │   Backend   │
│             │         │          │         │             │
│ 1. Request  │         │ 2. Route │         │ 3. Select   │
│ Completion  │         │   Event  │         │    Model    │
│             │         │          │         │             │
│ 6. Parse   │ <────── │ 5. Route │ <────── │ 4. Response │
│   Output    │         │ Response │         │    Event    │
└─────────────┘         └──────────┘         └─────────────┘
```

### Event Definitions

```python
from browser_use.event_bus import BaseEvent
from langchain_core.messages import BaseMessage
from pydantic import Field
from uuid_extensions import uuid7str
from typing import Any, Literal

class RequestLLMCompletionEvent(BaseEvent):
    """Request LLM completion from backend"""
    event_type: str = Field(default="RequestLLMCompletion", frozen=True)
    
    # Request details
    request_id: str = Field(default_factory=uuid7str)
    user_id: str
    
    # Model selection
    model_name: str | None = None  # e.g., "gpt-4.1", "gpt-4o", "claude-4-sonnet", "claude-4-opus", "gemini-2.5-pro"
    
    # Messages and context
    messages: list[dict[str, Any]]  # Serialized BaseMessage objects
    system_prompt: str | None = None
    
    # Tool calling configuration
    tool_calling_method: Literal["auto", "function_calling", "tools", "json_mode", "raw"] = "auto"
    tools: list[dict[str, Any]] | None = None  # Tool definitions
    output_schema: dict[str, Any] | None = None  # Expected output schema
    
    # Request parameters
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout_seconds: int = 120
    stream: bool = False
    
    # Metadata
    task_id: str | None = None  # For tracking/billing
    session_id: str | None = None
    request_metadata: dict[str, Any] = Field(default_factory=dict)


class LLMCompletionResponseEvent(BaseEvent):
    """LLM completion response from backend"""
    event_type: str = Field(default="LLMCompletionResponse", frozen=True)
    
    # Correlation
    request_id: str
    
    # Response content
    content: str | None = None  # Text response for raw mode
    tool_calls: list[dict[str, Any]] | None = None  # Tool calls if applicable
    parsed_output: dict[str, Any] | None = None  # Parsed structured output
    
    # Model metadata
    model_used: str  # Actual model that was used
    model_provider: str  # e.g., "openai", "anthropic"
    
    # Usage stats
    prompt_tokens: int
    completion_tokens: int
    total_cost: float | None = None  # Cost in USD if available
    
    # Timing
    latency_ms: int
    
    # Cache info
    cache_hit: bool = False
    cache_key: str | None = None


class LLMStreamChunkEvent(BaseEvent):
    """Streaming chunk for LLM responses"""
    event_type: str = Field(default="LLMStreamChunk", frozen=True)
    
    request_id: str
    chunk_index: int
    content: str | None = None
    tool_call_delta: dict[str, Any] | None = None
    is_final_chunk: bool = False


class LLMCompletionErrorEvent(BaseEvent):
    """Error response for LLM requests"""
    event_type: str = Field(default="LLMCompletionError", frozen=True)
    
    request_id: str
    error_code: str  # e.g., "RATE_LIMIT", "INVALID_REQUEST", "MODEL_UNAVAILABLE"
    error_message: str
    retry_after_seconds: int | None = None
    suggested_fallback_model: str | None = None
```

### CloudLLM Adapter

Create an adapter that implements the LangChain interface but routes through events:

```python
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import asyncio

class CloudLLM(BaseChatModel):
    """LLM that routes all calls through the event bus to a cloud backend"""
    
    event_bus: EventBus
    user_id: str
    model_name: str | None = None  # e.g., "gpt-4o", "claude-4-opus"
    timeout: int = 120
    
    @property
    def _llm_type(self) -> str:
        return "cloud_llm"
    
    def _generate(self, messages: list[BaseMessage], **kwargs) -> ChatResult:
        """Sync generation - converts to async internally"""
        return asyncio.run(self._agenerate(messages, **kwargs))
    
    async def _agenerate(self, messages: list[BaseMessage], **kwargs) -> ChatResult:
        """Async generation via event bus"""
        # Serialize messages
        serialized_messages = [
            {
                "type": msg.__class__.__name__,
                "content": msg.content,
                "additional_kwargs": getattr(msg, "additional_kwargs", {})
            }
            for msg in messages
        ]
        
        # Create request event
        request = RequestLLMCompletionEvent(
            user_id=self.user_id,
            model_name=self.model_name,
            messages=serialized_messages,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens"),
            stream=False,
        )
        
        # Set up response handler
        response_future = asyncio.Future()
        
        async def handle_response(event):
            if event.request_id == request.request_id:
                if isinstance(event, LLMCompletionResponseEvent):
                    response_future.set_result(event)
                else:  # Error event
                    response_future.set_exception(
                        RuntimeError(f"LLM request failed: {event.error_message}")
                    )
        
        # Subscribe to responses
        self.event_bus.on(LLMCompletionResponseEvent, handle_response)
        self.event_bus.on(LLMCompletionErrorEvent, handle_response)
        
        try:
            # Send request and wait
            await self.event_bus.enqueue(request)
            response = await asyncio.wait_for(response_future, timeout=self.timeout)
            
            # Convert to ChatResult
            message = AIMessage(
                content=response.content or "",
                additional_kwargs={
                    "model": response.model_used,
                    "usage": {
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                        "total_tokens": response.prompt_tokens + response.completion_tokens,
                    }
                }
            )
            
            if response.tool_calls:
                message.tool_calls = response.tool_calls
            
            generation = ChatGeneration(message=message)
            return ChatResult(generations=[generation])
            
        finally:
            # Cleanup - in production, properly unsubscribe handlers
            pass
    
    def with_structured_output(self, schema, **kwargs):
        """Support for structured output"""
        # Return a wrapped version that includes the schema in requests
        wrapped = CloudStructuredLLM(
            event_bus=self.event_bus,
            user_id=self.user_id,
            model_name=self.model_name,
            output_schema=schema.model_json_schema() if hasattr(schema, 'model_json_schema') else schema,
            timeout=self.timeout,
        )
        return wrapped
```

### Backend Handler

Implement a backend service that manages LLM routing:

```python
class RemoteLLMBackend:
    def __init__(self, event_bus: EventBus, model_registry: ModelRegistry):
        self.event_bus = event_bus
        self.model_registry = model_registry
        self.rate_limiter = RateLimiter()
        self.cache = LLMCache()
        
        # Subscribe to requests
        event_bus.on(RequestLLMCompletionEvent, self.handle_request)
    
    async def handle_request(self, event: RequestLLMCompletionEvent):
        """Process LLM request and send response"""
        try:
            # Check rate limits
            if not await self.rate_limiter.check_limit(event.user_id):
                await self._send_error(
                    event.request_id,
                    "RATE_LIMIT",
                    "Rate limit exceeded",
                    retry_after_seconds=60
                )
                return
            
            # Check cache
            cache_key = self.cache.generate_key(event)
            cached_response = await self.cache.get(cache_key)
            if cached_response:
                cached_response.request_id = event.request_id
                cached_response.cache_hit = True
                await self.event_bus.enqueue(cached_response)
                return
            
            # Get model from registry
            model = self.model_registry.get_model(
                name=event.model_name,
                capabilities=self._extract_required_capabilities(event)
            )
            
            # Deserialize messages
            messages = self._deserialize_messages(event.messages)
            
            # Call LLM
            start_time = time.time()
            
            if event.stream:
                # Handle streaming
                await self._handle_streaming(event, model, messages)
            else:
                # Regular completion
                result = await model.agenerate(messages, temperature=event.temperature)
                
                # Cache result
                response = LLMCompletionResponseEvent(
                    request_id=event.request_id,
                    content=result.generations[0].message.content,
                    tool_calls=getattr(result.generations[0].message, 'tool_calls', None),
                    model_used=model.model_name,
                    model_provider=model.provider,
                    prompt_tokens=result.llm_output.get('usage', {}).get('prompt_tokens', 0),
                    completion_tokens=result.llm_output.get('usage', {}).get('completion_tokens', 0),
                    latency_ms=int((time.time() - start_time) * 1000),
                )
                
                await self.cache.set(cache_key, response)
                await self.event_bus.enqueue(response)
                
        except Exception as e:
            await self._send_error(
                event.request_id,
                "INTERNAL_ERROR",
                str(e)
            )
    
    def _extract_required_capabilities(self, event: RequestLLMCompletionEvent) -> set[str]:
        """Determine required model capabilities from request"""
        capabilities = set()
        
        if event.tools:
            capabilities.add("tool_calling")
        if any("image" in str(msg) for msg in event.messages):
            capabilities.add("vision")
        if event.output_schema:
            capabilities.add("structured_output")
            
        return capabilities
```

### Integration with Agent

Modify the Agent to use CloudLLM:

```python
from browser_use import Agent
from browser_use.event_bus import EventBus, CloudLLM

async def main():
    # Create shared event bus
    event_bus = EventBus(name="cloud_llm_bus")
    await event_bus.start()
    
    # Set up backend handler (in production, runs on server)
    backend = RemoteLLMBackend(event_bus, model_registry)
    
    # Create LLM that routes through event bus
    llm = CloudLLM(
        event_bus=event_bus,
        user_id="user_123",
        model_name="gpt-4o",  # Specify exact model
    )
    
    # Use with agent
    agent = Agent(
        task="Find flights from NYC to London",
        llm=llm,  # All LLM calls now go through event bus
    )
    
    await agent.run()
    await event_bus.stop()
```

### Advanced Features

#### Model Fallback Chain

```python
class FallbackLLMBackend(RemoteLLMBackend):
    """Backend with automatic fallback to cheaper/faster models"""
    
    async def handle_request(self, event: RequestLLMCompletionEvent):
        # Get fallback chain for the requested model
        models = self.model_registry.get_fallback_chain(event.model_name or "gpt-4o")
        
        for i, model in enumerate(models):
            try:
                # Try each model in order
                result = await self._try_model(event, model)
                response = self._create_response(event, result, model)
                await self.event_bus.enqueue(response)
                return
                
            except ModelOverloadedError:
                if i < len(models) - 1:
                    # Try next model
                    continue
                else:
                    # All models failed
                    await self._send_error(
                        event.request_id,
                        "ALL_MODELS_UNAVAILABLE",
                        "All models in fallback chain are unavailable"
                    )
```

#### Usage Tracking

```python
class UsageTrackingBackend(RemoteLLMBackend):
    """Track usage per user/task for billing"""
    
    async def handle_request(self, event: RequestLLMCompletionEvent):
        # Process request
        response = await super().handle_request(event)
        
        # Track usage
        await self.usage_tracker.record(
            user_id=event.user_id,
            task_id=event.task_id,
            model=response.model_used,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost=response.total_cost,
        )
        
        # Emit usage event
        usage_event = LLMUsageRecordedEvent(
            user_id=event.user_id,
            task_id=event.task_id,
            total_tokens=response.prompt_tokens + response.completion_tokens,
            total_cost=response.total_cost,
        )
        await self.event_bus.enqueue(usage_event)
```

#### Prompt Caching

```python
class CachedLLMBackend(RemoteLLMBackend):
    """Smart caching based on semantic similarity"""
    
    async def handle_request(self, event: RequestLLMCompletionEvent):
        # Check semantic cache
        similar_requests = await self.semantic_cache.find_similar(
            event.messages,
            threshold=0.95
        )
        
        if similar_requests:
            # Reuse previous response with slight modification
            cached = similar_requests[0]
            response = cached.response.model_copy()
            response.request_id = event.request_id
            response.cache_hit = True
            response.cache_key = f"semantic:{cached.similarity_score}"
            
            await self.event_bus.enqueue(response)
            return
            
        # Continue with normal processing
        await super().handle_request(event)
```

### Benefits

1. **Centralized Model Management**: Backend controls which models are used
2. **Cost Optimization**: Route to cheaper models when appropriate
3. **Rate Limiting**: Enforce limits across all agents
4. **Usage Tracking**: Monitor costs and usage per user/task
5. **Caching**: Share responses across agents
6. **Fallback Logic**: Automatic failover when models are unavailable
7. **A/B Testing**: Test different models without changing agent code
8. **Compliance**: Ensure sensitive data goes only to approved models

## Cloud Integration

The event bus enables seamless cloud backend integration:

### WebSocket Bridge

Connect the event bus to a remote backend via WebSocket:

```python
class WebSocketEventBridge:
    def __init__(self, event_bus: EventBus, ws_url: str):
        self.event_bus = event_bus
        self.ws_url = ws_url
        
        # Forward local events to remote
        event_bus.on("*", self.forward_to_remote)
    
    async def forward_to_remote(self, event: BaseEvent):
        """Send events to remote backend"""
        if event.event_type.startswith("Create"):
            await self.ws.send_json(event.model_dump())
    
    async def receive_from_remote(self):
        """Receive events from remote backend"""
        async for msg in self.ws:
            event_data = json.loads(msg)
            event_type = EVENT_TYPE_MAP.get(event_data["event_type"])
            if event_type:
                event = event_type(**event_data)
                await self.event_bus.enqueue(event)
```

### HTTP Webhook Integration

Send events to HTTP endpoints:

```python
class HTTPEventForwarder:
    def __init__(self, event_bus: EventBus, endpoint: str):
        self.endpoint = endpoint
        
        # Subscribe to events to forward
        event_bus.on(CreateAgentSessionEvent, self.forward)
        event_bus.on(CreateAgentTaskEvent, self.forward)
        event_bus.on(CreateAgentStepEvent, self.forward)
    
    async def forward(self, event: BaseEvent):
        """POST event to HTTP endpoint"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.endpoint,
                json=event.model_dump(),
                headers={"X-Event-Type": event.event_type}
            )
            return {"status": response.status_code}
```

## Advanced Patterns

### Request-Response Correlation

Implement request-response patterns with correlation IDs:

```python
class RequestResponseBus:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.pending_requests: dict[str, asyncio.Future] = {}
    
    async def request(
        self, 
        request_event: BaseEvent, 
        response_type: type[BaseEvent],
        timeout: float = 30.0
    ) -> BaseEvent:
        """Send request and wait for correlated response"""
        request_id = request_event.request_id
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        
        # Set up response handler
        async def handle_response(event):
            if hasattr(event, 'request_id') and event.request_id == request_id:
                if request_id in self.pending_requests:
                    self.pending_requests[request_id].set_result(event)
        
        # Subscribe and send
        self.event_bus.on(response_type, handle_response)
        await self.event_bus.enqueue(request_event)
        
        try:
            # Wait for response
            response = await asyncio.wait_for(future, timeout)
            return response
        finally:
            # Cleanup
            self.pending_requests.pop(request_id, None)
```

### Event Replay

Replay events from write-ahead log:

```python
async def replay_events(source_bus: EventBus, target_bus: EventBus):
    """Replay all events from source to target"""
    events = source_bus.get_event_log()
    
    for event in events:
        # Clone event without completion tracking
        event_dict = event.model_dump(exclude={'started_at', 'completed_at', 'results', 'errors'})
        event_type = EVENT_TYPE_MAP.get(event.event_type, BaseEvent)
        cloned_event = event_type(**event_dict)
        
        await target_bus.enqueue(cloned_event)
```

### Event Filtering

Filter events before processing:

```python
class FilteredEventBus(EventBus):
    def __init__(self, *args, filter_func=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filter_func = filter_func or (lambda e: True)
    
    async def enqueue(self, event: BaseEvent) -> BaseEvent:
        """Only enqueue events that pass filter"""
        if self.filter_func(event):
            return await super().enqueue(event)
        return event  # Return without processing
```

### Event Aggregation

Aggregate multiple events into summaries:

```python
class EventAggregator:
    def __init__(self, event_bus: EventBus, window_seconds: float = 60):
        self.window_seconds = window_seconds
        self.step_count = 0
        self.last_summary = time.time()
        
        event_bus.on(StepCreatedEvent, self.track_step)
    
    async def track_step(self, event: StepCreatedEvent):
        self.step_count += 1
        
        # Emit summary periodically
        if time.time() - self.last_summary > self.window_seconds:
            summary = AgentActivitySummary(
                steps_per_minute=self.step_count * 60 / self.window_seconds,
                period_seconds=self.window_seconds,
            )
            await self.event_bus.enqueue(summary)
            
            self.step_count = 0
            self.last_summary = time.time()
```

## Best Practices

### 1. Event Design

- **Immutable event types**: Use `Field(frozen=True)` for event_type
- **Descriptive names**: Use clear, action-oriented event names
- **Include correlation IDs**: Add request_id for request-response patterns
- **Timestamp everything**: Include created_at, updated_at fields
- **Limit payload size**: Use references instead of embedding large data

### 2. Handler Implementation

- **Idempotent handlers**: Handlers should be safe to retry
- **Fast handlers**: Offload heavy work to background tasks
- **Error handling**: Return errors, don't raise exceptions
- **Timeout handling**: Set reasonable timeouts for remote calls

```python
async def robust_handler(event: BaseEvent):
    try:
        # Quick validation
        if not is_valid(event):
            return {"status": "skipped", "reason": "invalid"}
        
        # Offload heavy work
        task_id = await queue_background_task(event)
        
        return {"status": "queued", "task_id": task_id}
        
    except Exception as e:
        # Return error, don't raise
        return {"status": "error", "error": str(e)}
```

### 3. Performance Optimization

- **Batch operations**: Use `enqueue_batch_and_wait` for multiple events
- **Async handlers**: Prefer async handlers for I/O operations
- **Selective subscriptions**: Subscribe to specific events, not "*"
- **Event filtering**: Filter early to avoid unnecessary processing

### 4. Testing

Use real objects with pytest fixtures for testing:

```python
import pytest
from browser_use.event_bus import EventBus

@pytest.fixture
async def event_bus():
    """Create a real event bus for testing"""
    bus = EventBus(name="test_bus")
    await bus.start()
    yield bus
    await bus.stop()

@pytest.fixture
async def test_backend(event_bus):
    """Create test backend with real event bus"""
    # For S3 or external services, you can mock
    mock_s3 = Mock()
    
    # But use real objects for everything else
    browser_pool = BrowserPool()
    backend = RemoteBrowserBackend(event_bus, browser_pool)
    
    return backend

# Use in tests
async def test_browser_allocation(event_bus, test_backend):
    # Set up handler to capture responses
    responses = []
    
    async def capture_response(event):
        responses.append(event)
    
    event_bus.on(BrowserSessionReadyEvent, capture_response)
    
    # Send request
    request = RequestBrowserSessionEvent(
        user_id="test_user",
        browser_profile={},
    )
    await event_bus.enqueue(request)
    
    # Wait for processing
    await event_bus.wait_for_empty_queue()
    
    # Assert response received
    assert len(responses) == 1
    assert responses[0].request_id == request.request_id
```

### 5. Monitoring

Track event bus health:

```python
class MonitoredEventBus(EventBus):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.metrics = {
            "events_processed": 0,
            "events_failed": 0,
            "processing_time_ms": [],
        }
    
    async def _execute_handlers(self, event: BaseEvent) -> None:
        start = time.time()
        await super()._execute_handlers(event)
        
        duration_ms = (time.time() - start) * 1000
        self.metrics["processing_time_ms"].append(duration_ms)
        
        if event.errors:
            self.metrics["events_failed"] += 1
        else:
            self.metrics["events_processed"] += 1
```

## Security Considerations

1. **Validate events**: Always validate event data before processing
2. **Sanitize payloads**: Remove sensitive data before forwarding to external services
3. **Access control**: Implement handler-level authorization
4. **Rate limiting**: Prevent event flooding
5. **Audit logging**: Track who emits what events

```python
async def secure_handler(event: BaseEvent):
    # Validate event source
    if not is_authorized(event.user_id, event.event_type):
        return {"status": "unauthorized"}
    
    # Sanitize sensitive data
    safe_event = sanitize_event(event)
    
    # Process safely
    return await process_event(safe_event)
```

## Troubleshooting

### Common Issues

1. **Events not processing**: Ensure event bus is started with `await bus.start()`
2. **Handlers not firing**: Check event type matches exactly (case-sensitive)
3. **Deadlocks**: Avoid circular event dependencies
4. **Memory leaks**: Unsubscribe handlers when done
5. **Slow processing**: Check for blocking operations in handlers

### Debug Mode

Enable debug logging:

```python
import logging

# Enable debug logs
logging.getLogger("browser_use.event_bus").setLevel(logging.DEBUG)

# Create bus with descriptive name
bus = EventBus(name="debug_bus")

# Track all events
async def debug_handler(event: BaseEvent):
    print(f"[DEBUG] {event.event_type}: {event.model_dump()}")

bus.on("*", debug_handler)
```

## Future Enhancements

Planned improvements to the event bus:

1. **Event persistence**: Store events in database for recovery
2. **Event routing**: Route events to different backends based on rules
3. **Event versioning**: Handle event schema evolution
4. **Distributed events**: Multi-node event bus clustering
5. **GraphQL subscriptions**: Real-time event streaming to clients
6. **Event sourcing**: Build application state from event history

## Contributing

When adding new events or handlers:

1. Define events in appropriate module (`cloud_events.py`, etc.)
2. Document event fields and purpose
3. Add tests for new event types
4. Update this README with examples
5. Consider backward compatibility

The event bus is a core component of browser-use's extensibility. Use it to build powerful integrations while keeping the codebase modular and maintainable.