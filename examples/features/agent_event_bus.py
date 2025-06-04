"""
Example of using the EventBus with browser-use Agent.

This example demonstrates:
1. Setting up event handlers for cloud sync events
2. Tracking agent actions via cloud events
3. Serializing events for analysis
"""

import asyncio
import json
from pathlib import Path

import anyio
from langchain_openai import ChatOpenAI

from browser_use import Agent
from browser_use.event_bus import (
	CreateAgentOutputFileEvent,
	CreateAgentSessionEvent,
	CreateAgentStepEvent,
	CreateAgentTaskEvent,
)


# Example cloud sync handler
async def cloud_sync_handler(event):
	"""Handle cloud sync events"""
	event_type = event.event_type

	print(f'\n🌥️  Cloud Event: {event_type}')

	if event_type == 'CreateAgentSession':
		print(f'   Session ID: {event.id}')
		print(f'   User ID: {event.user_id}')
		print(f'   Browser Session ID: {event.browser_session_id}')
		# In real implementation, would POST to cloud API
		return {'cloud_id': 'cloud_session_123', 'synced': True}

	elif event_type == 'CreateAgentTask':
		print(f'   Task: {event.task}')
		print(f'   Agent Session ID: {event.agent_session_id}')
		print(f'   LLM Model: {event.llm_model}')
		return {'cloud_id': 'cloud_task_456', 'synced': True}

	elif event_type == 'CreateAgentStep':
		print(f'   Step #{event.step}')
		print(f'   Actions: {len(event.actions)}')
		print(f'   Next Goal: {event.next_goal}')
		return {'cloud_id': f'cloud_step_{event.step}', 'synced': True}

	elif event_type == 'CreateAgentOutputFile':
		print(f'   File: {event.file_name}')
		print(f'   Task ID: {event.task_id}')
		return {'cloud_id': 'cloud_file_789', 'synced': True}

	return {'synced': False}


# Performance tracking handler
async def performance_tracker(event):
	"""Track performance metrics for events"""
	if hasattr(event, 'started_at') and hasattr(event, 'completed_at'):
		if event.started_at and event.completed_at:
			duration = (event.completed_at - event.started_at).total_seconds()
			print(f'⏱️  {event.event_type} took {duration:.2f}s')
	return 'tracked'


async def main():
	# Create agent
	agent = Agent(
		task="Go to google.com and search for 'browser automation with AI'",
		llm=ChatOpenAI(model='gpt-4o-mini'),
	)

	# Subscribe handlers to specific event types
	agent.event_bus.on(CreateAgentSessionEvent, cloud_sync_handler)
	agent.event_bus.on(CreateAgentTaskEvent, cloud_sync_handler)
	agent.event_bus.on(CreateAgentStepEvent, cloud_sync_handler)
	agent.event_bus.on(CreateAgentOutputFileEvent, cloud_sync_handler)

	# Subscribe performance tracker to all events
	agent.event_bus.on('*', performance_tracker)

	# You can also subscribe by event type name
	async def on_step_created(event):
		if event.event_type == 'CreateAgentStep':
			print(f'\n🔍 Step {event.step} created at URL: {event.url}')
		return 'handled'

	agent.event_bus.on('CreateAgentStep', on_step_created)

	try:
		# Run the agent
		print('🚀 Starting agent with event tracking...\n')
		result = await agent.run(max_steps=3)

		# Get all events from the write-ahead log
		all_events = agent.event_bus.get_event_log()
		print(f'\n📊 Total events recorded: {len(all_events)}')

		# Group events by type
		event_types = {}
		for event in all_events:
			event_type = event.event_type
			event_types[event_type] = event_types.get(event_type, 0) + 1

		print('\n📈 Event Summary:')
		for event_type, count in event_types.items():
			print(f'   {event_type}: {count}')

		# Find events with errors
		error_events = [e for e in all_events if e.errors]
		if error_events:
			print(f'\n⚠️  Events with errors: {len(error_events)}')

		# Save events to file
		events_file = Path('agent_events.json')
		await agent.event_bus.serialize_events_to_file(events_file)
		print(f'\n💾 Events saved to {events_file}')

		# Example: Load and analyze specific events
		async with await anyio.open_file(events_file) as f:
			content = await f.read()
			saved_events = json.loads(content)

		# Find all step events
		step_events = [e for e in saved_events if e['event_type'] == 'CreateAgentStep']
		print(f'\n🔍 Found {len(step_events)} step events')

		for i, step in enumerate(step_events):
			print(f'\n   Step {i + 1}:')
			print(f'   - URL: {step.get("url", "N/A")}')
			print(f'   - Next Goal: {step.get("next_goal", "N/A")}')
			print(f'   - Memory: {step.get("memory", "N/A")[:100]}...')

			# Check if this step was synced to cloud
			if 'cloud_sync_handler' in step.get('results', {}):
				sync_result = step['results']['cloud_sync_handler']
				print(f'   - Cloud sync: {"✅" if sync_result.get("synced") else "❌"}')

	finally:
		# The event bus will be stopped automatically when agent closes
		pass


if __name__ == '__main__':
	asyncio.run(main())
