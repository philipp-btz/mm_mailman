from mattermostdriver import Driver
from config import MATTERMOST_URL, BOT_TOKEN, TEAM_NAME, WHITELIST, CHANNEL_GROUPS
from state import bot_info

driver = Driver({'url': MATTERMOST_URL, 'token': BOT_TOKEN, 'scheme': 'https', 'port': 443})


def initialize_driver():
    """Connects to Mattermost and fetches required system IDs."""
    driver.login()
    bot_info["bot_id"] = driver.users.get_user('me')['id']
    try:
        bot_info["team_id"] = driver.teams.get_team_by_name(TEAM_NAME)['id']
        print(f"Bot connected. Bot ID: {bot_info['bot_id']} | Team ID: {bot_info['team_id']}")
    except Exception as e:
        print(f"Failed to resolve Team ID for '{TEAM_NAME}'. Error: {e}")


def resolve_targets(requested_inputs):
    """Expands groups, checks the whitelist, and fetches channel IDs."""
    valid_ids, valid_names, invalid_names = [], [], []
    expanded_names = []

    # Expand group names into individual channels
    for item in requested_inputs:
        clean_item = item.strip().lower().strip('#')
        if clean_item in CHANNEL_GROUPS:
            expanded_names.extend(CHANNEL_GROUPS[clean_item])
        else:
            expanded_names.append(clean_item)

    # Deduplicate, validate against whitelist, and resolve IDs
    for name in set(expanded_names):
        if name not in WHITELIST:
            invalid_names.append(name)
            continue
        try:
            channel = driver.channels.get_channel_by_name(bot_info["team_id"], name)
            valid_ids.append(channel['id'])
            valid_names.append(name)
        except Exception:
            invalid_names.append(name)

    return valid_ids, valid_names, invalid_names