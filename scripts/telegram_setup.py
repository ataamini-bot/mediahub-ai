"""Configure an owner-created forum, with a durable journal for ambiguous replies."""
from ops_common import BotAPI, OperationError, TOPICS, ask, confirm, read_json, save_json


def ensure_topics(api, chat_id, journal_path, existing=None, recover=None):
    me = api.call("getMe")
    chat = api.call("getChat", chat_id=chat_id)
    if chat.get("type") != "supergroup" or not chat.get("is_forum"):
        raise OperationError("The chat must be a supergroup with Topics enabled.")
    member = api.call("getChatMember", chat_id=chat_id, user_id=me["id"])
    if member.get("status") != "administrator" or not member.get("can_manage_topics"):
        raise OperationError("Add the bot as a group administrator with the Manage Topics permission.")
    journal = read_json(journal_path, {})
    identity = {"bot_id": me["id"], "chat_id": int(chat["id"])}
    if any(journal.get(key) != value for key, value in identity.items()):
        journal = {**identity, "topics": dict(existing or {}), "pending": None}
        save_json(journal_path, journal)
    # The database remains authoritative after an administrator edits a route.
    for key, value in (existing or {}).items():
        if key in TOPICS and value:
            journal["topics"][key] = int(value)
    save_json(journal_path, journal)
    for key, title in TOPICS.items():
        if journal["topics"].get(key):
            continue
        if journal.get("pending") == key:
            if recover is None:
                raise OperationError(f"The result of creating {title} is unknown. Enter the existing topic ID from Telegram.")
            previous = recover(title)
            if previous:
                journal["topics"][key] = int(previous)
                journal["pending"] = None
                save_json(journal_path, journal)
                continue
        # Save before sending: a lost HTTP reply must never cause an automatic duplicate.
        journal["pending"] = key
        save_json(journal_path, journal)
        result = api.call("createForumTopic", chat_id=identity["chat_id"], name=title)
        journal["topics"][key] = int(result["message_thread_id"])
        journal["pending"] = None
        save_json(journal_path, journal)
    return {"chat_id": identity["chat_id"], "topics": journal["topics"]}


def recover_topic(title):
    print(f"Creation of {title} was already attempted. Check the group; the previous attempt may have succeeded.")
    value = ask("Existing topic ID (enter NEW only if the topic was not created)", pattern=r"[1-9][0-9]*|NEW")
    return None if value == "NEW" else int(value)


def configure_forum(token, journal_path, current, api=None):
    print("Create a Telegram supergroup, enable Topics, and add the bot as an administrator with Manage Topics.")
    chat_id = ask("Supergroup ID (starting with -100)", current.get("chat_id"), pattern=r"-100[0-9]+")
    api = api or BotAPI(token)
    info = api.call("getChat", chat_id=chat_id)
    print("Selected group: " + info.get("title", "") + " (" + str(info["id"]) + ")")
    same = str(current.get("chat_id")) == str(info["id"])
    topics = dict(current.get("topics", {})) if same else {}
    confirm("Keep the saved topics and create any missing topics in this group?")
    return ensure_topics(api, int(chat_id), journal_path, topics, recover_topic)


def required_channel(token, api=None):
    api = api or BotAPI(token)
    chat_id = ask("Required channel ID or @username", pattern=r"-100[0-9]+|@[A-Za-z][A-Za-z0-9_]{3,}")
    chat = api.call("getChat", chat_id=chat_id)
    if chat.get("type") not in {"channel", "supergroup"}:
        raise OperationError("Select a channel or supergroup.")
    me = api.call("getMe")
    member = api.call("getChatMember", chat_id=chat["id"], user_id=me["id"])
    if member.get("status") != "administrator":
        raise OperationError("The bot must be a channel administrator to check user membership.")
    language = ask("Audience language: fa / en / all", "all", pattern=r"fa|en|all")
    link = "https://t.me/" + chat["username"] if chat.get("username") else ask(
        "Private channel invite link", pattern=r"https://t\.me/\+[-A-Za-z0-9_]+|https://t\.me/joinchat/[-A-Za-z0-9_]+")
    confirm(f"Add {chat.get('title', '')} for audience language {language}?")
    return {"chat_id": str(chat["id"]), "title": chat["title"][:120], "invite_url": link,
            "language": language, "sort_order": 0, "is_active": True}
