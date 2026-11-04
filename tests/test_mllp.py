import asyncio

from hl7fhir.config import Settings
from hl7fhir.mllp import END_BLOCK, START_BLOCK, MLLPServer, frame, send_message
from hl7fhir.service import process_message


async def start_server(settings: Settings) -> MLLPServer:
    async def processor(text):
        return await process_message(text, settings)

    server = MLLPServer(settings, processor)
    await server.start(host="127.0.0.1", port=0)
    return server


async def read_ack(reader) -> str:
    data = await asyncio.wait_for(reader.readuntil(END_BLOCK), 5)
    return data[data.find(START_BLOCK) + 1 : -len(END_BLOCK)].decode()


def test_send_and_receive_ack(sample, settings):
    async def scenario():
        server = await start_server(settings)
        try:
            return await send_message("127.0.0.1", server.port, sample("adt_a01.hl7"))
        finally:
            await server.close()

    ack = asyncio.run(scenario())
    assert "MSA|AA|MSG00001" in ack


def test_multiple_messages_and_split_frames_on_one_connection(sample, settings):
    async def scenario():
        server = await start_server(settings)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            first = frame(sample("adt_a01.hl7"))
            writer.write(first[:25])  # frame split across TCP packets
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.write(first[25:] + frame(sample("oru_r01.hl7")) + frame("garbage"))
            await writer.drain()
            acks = [await read_ack(reader) for _ in range(3)]
            writer.close()
            await writer.wait_closed()
            return acks
        finally:
            await server.close()

    acks = asyncio.run(scenario())
    assert "MSA|AA|MSG00001" in acks[0]
    assert "MSA|AA|LAB778899" in acks[1]
    assert "MSA|AR" in acks[2]


def test_oversized_message_closes_connection(settings):
    small = Settings(max_message_bytes=50)

    async def scenario():
        server = await start_server(small)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(START_BLOCK + b"X" * 200)
            await writer.drain()
            ack = await read_ack(reader)
            writer.close()
            return ack
        finally:
            await server.close()

    assert "MSA|AR||Message too large" in asyncio.run(scenario())
