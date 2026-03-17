import json
import time
import asyncio
import threading
import uvicorn
from fastapi import FastAPI, Request

from config import WEBHOOK_URL, WHITELIST, CHANNEL_GROUPS, SESSION_TIMEOUT_SECONDS, CLEANUP_INTERVAL_SECONDS
from state import sessions, known_users, bot_info
from mattermost import driver, initialize_driver, resolve_targets

app = FastAPI()


# --- Background Task ---
async def session_cleanup_task():
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        current_time = time.time()
        expired_users = [uid for uid, sess in sessions.items() if
                         current_time - sess['timestamp'] > SESSION_TIMEOUT_SECONDS]

        for user_id in expired_users:
            expired_session = sessions.pop(user_id)
            try:
                driver.posts.create_post({
                    'channel_id': expired_session['dm_channel_id'],
                    'message': "⏱️ **Session expired.** You took too long to confirm. Send a new message to start over."
                })
            except Exception as e:
                print(f"Failed to send timeout notice: {e}")


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(session_cleanup_task())


# --- HTTP Endpoint for Buttons ---
@app.post("/action")
async def handle_button_action(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    sender_name = data.get('user_name')
    action = data.get('context', {}).get('action')

    session = sessions.get(user_id)
    if not session:
        return {"ephemeral_text": "Session expired or invalid. Please send a new message."}

    if action == 'send':
        for channel_id in session['target_ids']:
            try:
                driver.posts.create_post({
                    'channel_id': channel_id,
                    'message': f"📢 **Broadcast from @{sender_name}**\n\n> {session['message']}"
                })
            except Exception as e:
                print(f"Failed to post to {channel_id}: {e}")

        del sessions[user_id]
        return {"update": {"message": "✅ **Broadcast sent successfully.**", "props": {}}}

    elif action == 'cancel':
        del sessions[user_id]
        return {"update": {"message": "❌ **Broadcast canceled.**", "props": {}}}


# --- WebSocket DM Handler ---
async def message_handler(message):
    if message.get('event') != 'posted': return
    data = message.get('data', {})
    if data.get('channel_type') != 'D': return

    post_data = json.loads(data.get('post', '{}'))
    sender_id = post_data.get('user_id')
    dm_channel_id = post_data.get('channel_id')
    text = post_data.get('message').strip()

    if sender_id == bot_info["bot_id"]: return

    if sender_id not in sessions:
        welcome_text = ""
        if sender_id not in known_users:
            known_users.add(sender_id)
            welcome_text = "👋 **Welcome to the Broadcast Bot!**\nSend me a message to start a broadcast.\n\n"

        sessions[sender_id] = {
            "state": "AWAITING_CHANNELS",
            "message": text,
            "timestamp": time.time(),
            "dm_channel_id": dm_channel_id
        }

        group_list = ', '.join(CHANNEL_GROUPS.keys())
        driver.posts.create_post({
            'channel_id': dm_channel_id,
            'message': f"{welcome_text}I've captured your message. Reply with **channel names** or **groups** separated by commas.\n"
                       f"*Groups:* {group_list}\n*Allowed Channels:* {', '.join(WHITELIST)}"
        })
        return

    session = sessions[sender_id]
    if session["state"] == "AWAITING_CHANNELS":
        requested_inputs = text.split(',')
        valid_ids, valid_names, invalid_names = resolve_targets(requested_inputs)

        if not valid_ids:
            driver.posts.create_post({
                'channel_id': dm_channel_id,
                'message': "⚠️ No valid channels found. Please try again."
            })
            return

        session["target_ids"] = valid_ids
        session["state"] = "CONFIRMATION"
        session["timestamp"] = time.time()

        warning_text = f"\n⚠️ *Ignored invalid channels: {', '.join(invalid_names)}*" if invalid_names else ""
        attachments = [{
            "text": f"**Preview:**\n> {session['message']}\n\n**Targets:** {', '.join(valid_names)}{warning_text}",
            "actions": [
                {
                    "id": "btn_send", "name": "✅ Send Broadcast",
                    "integration": {"url": WEBHOOK_URL, "context": {"action": "send"}}
                },
                {
                    "id": "btn_cancel", "name": "❌ Cancel",
                    "integration": {"url": WEBHOOK_URL, "context": {"action": "cancel"}}
                }
            ]
        }]

        driver.posts.create_post({
            'channel_id': dm_channel_id,
            'message': "Please confirm your broadcast order:",
            'props': {"attachments": attachments}
        })


def run_websocket():
    driver.init_websocket(message_handler)


if __name__ == "__main__":
    initialize_driver()
    ws_thread = threading.Thread(target=run_websocket, daemon=True)
    ws_thread.start()

    uvicorn.run(app, host="0.0.0.0", port=8000)