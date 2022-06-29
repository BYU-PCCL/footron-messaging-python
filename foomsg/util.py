import asyncio


def call_possibly_async_handlers(handlers, *args, **kwargs):
    event_loop = asyncio.get_event_loop()
    for handler in handlers:
        if asyncio.iscoroutinefunction(handler):
            event_loop.create_task(handler(*args, **kwargs))
        else:
            handler(*args, **kwargs)
