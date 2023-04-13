import trio

async def double_sleep(x):
    await trio.sleep(2 * x)
    print(x)

# trio.run(double_sleep, 3)  # does nothing for 6 seconds then returns
# trio.run(double_sleep, 2)
# trio.run(double_sleep, 1)


async def parent():
    print("parent started!")
    async with trio.open_nursery() as nursery:
        nursery.start_soon(double_sleep, 3)
        nursery.start_soon(double_sleep, 2)
        nursery.start_soon(double_sleep, 1)
        nursery.start_soon(double_sleep, 4)
    print('Closed nursery')

trio.run(parent)