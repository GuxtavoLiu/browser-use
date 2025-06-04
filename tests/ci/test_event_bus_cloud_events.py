"""
Tests for cloud events emitted during agent lifecycle.

This test file ensures that all cloud events defined in cloud_events.py
are properly emitted at the correct times during agent execution.
"""

import base64
import json
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

# Skip LLM API key verification for tests
os.environ['SKIP_LLM_API_KEY_VERIFICATION'] = 'true'

from browser_use.event_bus import (
	BaseEvent,
	CreateAgentOutputFileEvent,
	CreateAgentSessionEvent,
	CreateAgentStepEvent,
	CreateAgentTaskEvent,
	CreateUserBrowserProfileEvent,
	CreateUserUploadedFileEvent,
	EventBus,
)


# Test fixtures
@pytest.fixture
def mock_llm():
	"""Create a mock LLM that returns predictable responses"""
	llm = MagicMock(spec=BaseChatModel)

	# Create a mock response
	mock_response = AIMessage(
		content=json.dumps(
			{
				'current_state': {
					'evaluation_previous_goal': 'Starting task',
					'memory': 'No previous actions',
					'next_goal': 'Complete the task',
				},
				'action': [{'done': {'success': True, 'text': 'Task completed successfully'}}],
			}
		)
	)

	# Make the LLM return our mock response
	llm.ainvoke = AsyncMock(return_value=mock_response)
	llm.invoke = Mock(return_value=mock_response)

	# Mock the with_structured_output method
	structured_llm = MagicMock()
	structured_llm.ainvoke = AsyncMock(
		return_value={
			'raw': mock_response,
			'parsed': MagicMock(
				current_state=MagicMock(
					evaluation_previous_goal='Starting task', memory='No previous actions', next_goal='Complete the task'
				),
				action=[MagicMock()],
			),
		}
	)
	llm.with_structured_output = Mock(return_value=structured_llm)

	# Set attributes that agent checks
	llm.model_name = 'gpt-4o'
	llm._verified_api_keys = True
	llm._verified_tool_calling_method = 'function_calling'

	return llm


@pytest.fixture
def event_collector():
	"""Collect all events emitted during tests"""
	events = []

	class EventCollector:
		def __init__(self):
			self.events = events
			self.event_types = set()

		async def collect_event(self, event: BaseEvent):
			self.events.append(event)
			self.event_types.add(event.event_type)
			return 'collected'

		def get_events_by_type(self, event_type: str) -> list[BaseEvent]:
			return [e for e in self.events if e.event_type == event_type]

		def clear(self):
			self.events.clear()
			self.event_types.clear()

	return EventCollector()


class TestCloudEventsEmission:
	"""Test that cloud events are properly emitted during agent lifecycle"""

	async def test_agent_session_event(self, event_collector):
		"""Test CreateAgentSessionEvent creation and validation"""
		# Create event directly
		event = CreateAgentSessionEvent(
			id='test-session-id',
			user_id='',
			browser_session_id='test-browser-session-id',
			browser_session_live_url='',
			browser_session_cdp_url='',
			browser_state={
				'viewport': {'width': 1920, 'height': 1080},
				'user_agent': 'Mozilla/5.0 Test User Agent',
				'headless': True,
			},
		)

		# Test validation
		assert event.id == 'test-session-id'
		assert event.user_id == ''
		assert event.browser_session_id == 'test-browser-session-id'
		assert event.browser_session_live_url == ''
		assert event.browser_session_cdp_url == ''
		assert event.browser_state['viewport']['width'] == 1920
		assert event.browser_state['viewport']['height'] == 1080
		assert event.browser_state['user_agent'] == 'Mozilla/5.0 Test User Agent'
		assert not event.browser_session_stopped

		# Test event bus integration
		bus = EventBus()
		await bus.start()
		bus.on('*', event_collector.collect_event)

		# Emit event
		await bus.enqueue(event)
		await bus.wait_for_empty_queue()

		# Check collection
		events = event_collector.get_events_by_type('CreateAgentSession')
		assert len(events) == 1

		# Compare key fields instead of entire object
		collected_event = events[0]
		assert collected_event.id == event.id
		assert collected_event.user_id == event.user_id
		assert collected_event.browser_session_id == event.browser_session_id
		assert collected_event.browser_session_live_url == event.browser_session_live_url
		assert collected_event.browser_session_cdp_url == event.browser_session_cdp_url
		assert collected_event.browser_state == event.browser_state
		assert collected_event.browser_session_stopped == event.browser_session_stopped

		await bus.stop()

	async def test_agent_task_event(self, event_collector):
		"""Test CreateAgentTaskEvent creation and validation"""
		# Create event directly
		session_id = str(uuid.uuid4())
		task_id = str(uuid.uuid4())

		event = CreateAgentTaskEvent(
			id=task_id,
			user_id='',
			agent_session_id=session_id,
			task='Test task',
			llm_model='gpt-4o',
			done_output='Task completed successfully',
			started_at=datetime.utcnow(),
			finished_at=datetime.utcnow(),
			agent_state={'n_steps': 1},
		)

		# Test validation
		assert event.id == task_id
		assert event.user_id == ''
		assert event.agent_session_id == session_id
		assert event.task == 'Test task'
		assert event.done_output == 'Task completed successfully'
		assert event.llm_model == 'gpt-4o'
		assert not event.stopped
		assert not event.paused
		assert event.started_at is not None
		assert event.finished_at is not None
		assert isinstance(event.agent_state, dict)

		# Test event bus integration
		bus = EventBus()
		await bus.start()
		bus.on('*', event_collector.collect_event)

		# Emit event
		await bus.enqueue(event)
		await bus.wait_for_empty_queue()

		# Check collection
		events = event_collector.get_events_by_type('CreateAgentTask')
		assert len(events) == 1

		# Compare key fields instead of entire object
		collected_event = events[0]
		assert collected_event.id == event.id
		assert collected_event.user_id == event.user_id
		assert collected_event.agent_session_id == event.agent_session_id
		assert collected_event.task == event.task
		assert collected_event.llm_model == event.llm_model
		assert collected_event.done_output == event.done_output

		await bus.stop()

	async def test_agent_step_event(self, event_collector):
		"""Test CreateAgentStepEvent creation and validation"""
		task_id = str(uuid.uuid4())

		# Create multiple step events
		events_to_emit = []
		for i in range(1, 4):
			event = CreateAgentStepEvent(
				id=str(uuid.uuid4()),
				user_id='',
				agent_task_id=task_id,
				step=i,
				evaluation_previous_goal=f'Evaluated step {i - 1}',
				memory=f'Memory from step {i}',
				next_goal=f'Goal for step {i + 1}',
				actions=[
					{
						'name': 'click' if i == 1 else 'type' if i == 2 else 'done',
						'args': {'selector': 'button#submit'}
						if i == 1
						else {'text': 'Hello world'}
						if i == 2
						else {'text': 'Task completed'},
					}
				],
				url='https://example.com',
				screenshot_url='data:image/png;base64,iVBORw0KGgo=',
			)
			events_to_emit.append(event)

		# Test event bus integration
		bus = EventBus()
		await bus.start()
		bus.on('*', event_collector.collect_event)

		# Emit all events
		for event in events_to_emit:
			await bus.enqueue(event)

		await bus.wait_for_empty_queue()

		# Check step events
		step_events = event_collector.get_events_by_type('CreateAgentStep')
		assert len(step_events) == 3  # One for each action

		# Check first step
		step1 = step_events[0]
		assert isinstance(step1, CreateAgentStepEvent)
		assert step1.step == 1
		assert step1.user_id == ''  # Empty string, to be filled by cloud handler
		assert step1.agent_task_id == task_id
		assert len(step1.actions) == 1
		assert step1.actions[0]['name'] == 'click'
		assert step1.evaluation_previous_goal == 'Evaluated step 0'
		assert step1.next_goal == 'Goal for step 2'
		assert step1.url == 'https://example.com'
		assert step1.screenshot_url is not None
		assert step1.screenshot_url.startswith('data:image/png;base64,')

		# Check step numbers increment
		assert step_events[1].step == 2
		assert step_events[2].step == 3

		await bus.stop()

	async def test_agent_output_file_event_gif(self, event_collector):
		"""Test CreateAgentOutputFileEvent for GIF generation"""
		task_id = str(uuid.uuid4())

		# Create GIF content
		gif_content = b'GIF89a\x01\x00\x01\x00\x00\x00\x00;'  # Minimal GIF
		gif_base64 = base64.b64encode(gif_content).decode('utf-8')

		event = CreateAgentOutputFileEvent(
			id=str(uuid.uuid4()),
			user_id='',
			task_id=task_id,
			file_name='test.gif',
			file_content=gif_base64,
			content_type='image/gif',
		)

		# Test validation
		assert event.user_id == ''
		assert event.task_id == task_id
		assert event.file_name == 'test.gif'
		assert event.content_type == 'image/gif'
		assert event.file_content == gif_base64

		# Test event bus integration
		bus = EventBus()
		await bus.start()
		bus.on('*', event_collector.collect_event)

		# Emit event
		await bus.enqueue(event)
		await bus.wait_for_empty_queue()

		# Check collection
		events = event_collector.get_events_by_type('CreateAgentOutputFile')
		assert len(events) == 1

		# Compare key fields instead of entire object
		collected_event = events[0]
		assert collected_event.task_id == event.task_id
		assert collected_event.file_name == event.file_name
		assert collected_event.file_content == event.file_content
		assert collected_event.content_type == event.content_type

		await bus.stop()

	async def test_user_uploaded_file_event(self, event_collector):
		"""Test CreateUserUploadedFileEvent structure and validation"""
		# This event is not emitted by the agent itself, but we can test its structure
		event = CreateUserUploadedFileEvent(
			user_id='test_user',
			task_id='0683fb03-c5da-79c9-8000-d3a39c47c659',
			file_name='document.pdf',
			file_content=base64.b64encode(b'PDF content').decode('utf-8'),
			content_type='application/pdf',
		)

		assert event.event_type == 'CreateUserUploadedFile'
		assert event.user_id == 'test_user'
		assert event.file_name == 'document.pdf'
		assert event.content_type == 'application/pdf'
		assert event.created_at is not None

		# Test file size validation (50MB limit)
		# Create 60MB of raw data, which exceeds the 50MB limit
		large_content = base64.b64encode(b'x' * 60_000_000).decode('utf-8')
		with pytest.raises(ValueError, match='exceeds maximum size'):
			CreateUserUploadedFileEvent(
				user_id='test_user',
				task_id='0683fb03-c5da-79c9-8000-d3a39c47c659',
				file_name='large.bin',
				file_content=large_content,
			)

	async def test_user_browser_profile_event(self, event_collector):
		"""Test CreateUserBrowserProfileEvent structure"""
		# This event is not emitted by the agent itself, but we can test its structure
		event = CreateUserBrowserProfileEvent(user_id='test_user', profile_id='profile_123')

		assert event.event_type == 'CreateUserBrowserProfile'
		assert event.user_id == 'test_user'
		assert event.profile_id == 'profile_123'
		assert event.id is not None  # Auto-generated
		assert event.created_at is not None

	async def test_all_events_have_required_fields(self, event_collector):
		"""Test that all emitted events have required base fields"""
		# Create a few events
		events_to_test = [
			CreateAgentSessionEvent(
				id='test-session',
				user_id='',
				browser_session_id='test-browser',
				browser_session_live_url='',
				browser_session_cdp_url='',
			),
			CreateAgentTaskEvent(
				user_id='',
				agent_session_id='test-session',
				task='test',
				llm_model='gpt-4o',
			),
			CreateAgentStepEvent(
				user_id='',
				agent_task_id='test-task',
				step=1,
				evaluation_previous_goal='eval',
				memory='mem',
				next_goal='next',
				actions=[],
			),
		]

		# Check all events have required fields
		for event in events_to_test:
			# Base event fields
			assert isinstance(event, BaseEvent)
			assert event.event_type is not None
			assert event.event_id is not None
			assert event.event_at is not None
			assert isinstance(event.event_path, list)

			# Check event_id is a valid UUID string
			try:
				# Should be able to parse as UUID
				uuid_obj = UUID(event.event_id)
				assert str(uuid_obj) == event.event_id
			except ValueError:
				pytest.fail(f'Invalid UUID in event_id: {event.event_id}')


class TestEventValidation:
	"""Test event validation and constraints"""

	def test_max_string_length_validation(self):
		"""Test that string fields enforce max length"""
		# Create event with very long task
		long_task = 'x' * 10000  # Longer than MAX_TASK_LENGTH (5000)

		# Should raise validation error for string too long
		with pytest.raises(ValueError, match='String should have at most 5000 characters'):
			CreateAgentTaskEvent(
				user_id='test', agent_session_id='0683fb03-c5da-79c9-8000-d3a39c47c659', llm_model='test-model', task=long_task
			)

	def test_screenshot_size_validation(self):
		"""Test screenshot size validation"""
		# screenshot_url has max_length of 2000 characters (MAX_URL_LENGTH)
		# Create a screenshot URL that exceeds this limit
		large_image = 'data:image/png;base64,' + 'x' * 2500  # Exceeds 2000 char limit

		# Should raise validation error for string too long
		with pytest.raises(ValueError, match='String should have at most 2000 characters'):
			CreateAgentStepEvent(
				user_id='test',
				agent_task_id='0683fb03-c5da-79c9-8000-d3a39c47c659',
				step=1,
				evaluation_previous_goal='test',
				memory='test',
				next_goal='test',
				actions=[],
				screenshot_url=large_image,
			)

	def test_event_type_frozen_field(self):
		"""Test that event_type fields are frozen"""
		event = CreateAgentTaskEvent(
			user_id='test', agent_session_id='0683fb03-c5da-79c9-8000-d3a39c47c659', llm_model='test-model', task='test'
		)

		# Should not be able to change event_type
		with pytest.raises(ValueError):
			event.event_type = 'DifferentType'


class TestEventBusIntegration:
	"""Test EventBus integration with cloud events"""

	async def test_event_bus_processes_all_cloud_events(self):
		"""Test that EventBus can process all types of cloud events"""
		bus = EventBus()
		await bus.start()

		collected = []

		async def collect(event: BaseEvent):
			collected.append(event)
			return 'ok'

		bus.on('*', collect)

		try:
			# Emit one of each event type
			events = [
				CreateAgentSessionEvent(
					id='0683fb03-c5da-79c9-8000-d3a39c47c659',
					user_id='test',
					browser_session_id='session1',
					browser_session_live_url='https://example.com',
					browser_session_cdp_url='ws://localhost:9222',
				),
				CreateAgentTaskEvent(
					user_id='test',
					agent_session_id='0683fb03-c5da-79c9-8000-d3a39c47c659',
					llm_model='test-model',
					task='test task',
				),
				CreateAgentStepEvent(
					user_id='test',
					agent_task_id='0683fb03-c5da-79c9-8000-d3a39c47c659',
					step=1,
					evaluation_previous_goal='eval',
					memory='memory',
					next_goal='goal',
					actions=[{'name': 'test'}],
				),
				CreateAgentOutputFileEvent(
					user_id='test',
					task_id='0683fb03-c5da-79c9-8000-d3a39c47c659',
					file_name='test.txt',
					file_content=base64.b64encode(b'test').decode(),
				),
				CreateUserUploadedFileEvent(
					user_id='test',
					task_id='0683fb03-c5da-79c9-8000-d3a39c47c659',
					file_name='upload.txt',
					file_content=base64.b64encode(b'upload').decode(),
				),
				CreateUserBrowserProfileEvent(user_id='test', profile_id='profile1'),
			]

			# Emit all events
			for event in events:
				await bus.enqueue(event)

			# Wait for processing
			await bus.wait_for_empty_queue()

			# Check all were collected
			assert len(collected) == len(events)
			event_types = {e.event_type for e in collected}
			expected_types = {
				'CreateAgentSession',
				'CreateAgentTask',
				'CreateAgentStep',
				'CreateAgentOutputFile',
				'CreateUserUploadedFile',
				'CreateUserBrowserProfile',
			}
			assert event_types == expected_types

		finally:
			await bus.stop()
