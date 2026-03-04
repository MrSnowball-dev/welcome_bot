"""
Telethon Media ID — pack/unpack/send media without re-uploading.

Standalone utility that creates persistent, serializable media references
from Telegram messages. Handles file_reference refresh automatically.

Usage:
    from telethon_media_id import pack_media_id, unpack_media_id, send_media

    # Pack from a message
    media_id = pack_media_id(event.message)
    save_to_db(media_id)

    # Send later (even after restart)
    sent_msg, updated_id = await send_media(client, chat, media_id)
    if updated_id != media_id:
        update_in_db(updated_id)  # file_reference was refreshed
"""

import struct
import base64
import logging

from telethon.tl import types
from telethon.errors import FileReferenceExpiredError

logger = logging.getLogger(__name__)

_VERSION = 1
_TYPE_DOCUMENT = 0
_TYPE_PHOTO = 1

# struct format: version(B) type(B) dc_id(i) id(q) access_hash(q)
# then: file_ref_len(H) file_reference(variable) chat_id(q) message_id(i)
_HEADER_FMT = '<BBiqq'
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_FOOTER_FMT = '<qi'
_FOOTER_SIZE = struct.calcsize(_FOOTER_FMT)


def _extract_media(media):
    """Extract (type_code, document_or_photo) from various Telethon objects."""
    if isinstance(media, types.Message):
        if media.media is None:
            raise ValueError("Message has no media")
        return _extract_media(media.media)
    if isinstance(media, types.MessageMediaDocument):
        if media.document is None:
            raise ValueError("MessageMediaDocument has no document")
        return _TYPE_DOCUMENT, media.document
    if isinstance(media, types.MessageMediaPhoto):
        if media.photo is None:
            raise ValueError("MessageMediaPhoto has no photo")
        return _TYPE_PHOTO, media.photo
    if isinstance(media, types.Document):
        return _TYPE_DOCUMENT, media
    if isinstance(media, types.Photo):
        return _TYPE_PHOTO, media
    raise TypeError(f"Unsupported media type: {type(media).__name__}")


def pack_media_id(media, chat_id=None, message_id=None):
    """Pack a Telethon media object into a URL-safe base64 string.

    Args:
        media: A Message, MessageMediaDocument, MessageMediaPhoto,
               Document, or Photo object.
        chat_id: Source chat ID for file_reference refresh.
                 Auto-extracted from Message objects.
        message_id: Source message ID for file_reference refresh.
                    Auto-extracted from Message objects.

    Returns:
        URL-safe base64 string that can be persisted and used later.
    """
    if isinstance(media, types.Message):
        if chat_id is None:
            chat_id = media.chat_id
        if message_id is None:
            message_id = media.id

    type_code, obj = _extract_media(media)

    file_ref = obj.file_reference or b''
    header = struct.pack(
        _HEADER_FMT,
        _VERSION, type_code, obj.dc_id, obj.id, obj.access_hash
    )
    ref_len = struct.pack('<H', len(file_ref))
    footer = struct.pack(_FOOTER_FMT, chat_id or 0, message_id or 0)

    raw = header + ref_len + file_ref + footer
    return base64.urlsafe_b64encode(raw).decode('ascii')


def unpack_media_id(media_id_str):
    """Unpack a packed media ID string into its components.

    Returns:
        dict with keys: type, dc_id, id, access_hash,
                        file_reference, chat_id, message_id
    """
    raw = base64.urlsafe_b64decode(media_id_str)

    version, type_code, dc_id, obj_id, access_hash = struct.unpack_from(
        _HEADER_FMT, raw, 0
    )
    if version != _VERSION:
        raise ValueError(f"Unsupported media ID version: {version}")

    offset = _HEADER_SIZE
    ref_len = struct.unpack_from('<H', raw, offset)[0]
    offset += 2
    file_ref = raw[offset:offset + ref_len]
    offset += ref_len

    chat_id, message_id = struct.unpack_from(_FOOTER_FMT, raw, offset)

    return {
        'type': 'document' if type_code == _TYPE_DOCUMENT else 'photo',
        'dc_id': dc_id,
        'id': obj_id,
        'access_hash': access_hash,
        'file_reference': file_ref,
        'chat_id': chat_id if chat_id != 0 else None,
        'message_id': message_id if message_id != 0 else None,
    }


def _repack(data):
    """Repack a dict (from unpack_media_id) back into a media ID string."""
    type_code = _TYPE_DOCUMENT if data['type'] == 'document' else _TYPE_PHOTO
    file_ref = data['file_reference'] or b''
    header = struct.pack(
        _HEADER_FMT,
        _VERSION, type_code, data['dc_id'], data['id'], data['access_hash']
    )
    ref_len = struct.pack('<H', len(file_ref))
    footer = struct.pack(_FOOTER_FMT, data['chat_id'] or 0, data['message_id'] or 0)
    return base64.urlsafe_b64encode(header + ref_len + file_ref + footer).decode('ascii')


def to_input_media(media_id_str):
    """Convert a packed media ID to an InputMedia TL object for send_file().

    Returns:
        InputMediaDocument or InputMediaPhoto ready for client.send_file().
    """
    data = unpack_media_id(media_id_str)

    if data['type'] == 'document':
        input_doc = types.InputDocument(
            id=data['id'],
            access_hash=data['access_hash'],
            file_reference=data['file_reference']
        )
        return types.InputMediaDocument(id=input_doc)
    else:
        input_photo = types.InputPhoto(
            id=data['id'],
            access_hash=data['access_hash'],
            file_reference=data['file_reference']
        )
        return types.InputMediaPhoto(id=input_photo)


async def _refresh_file_reference(client, data):
    """Re-fetch the source message and extract a fresh file_reference.

    Returns updated data dict, or raises if refresh is not possible.
    """
    chat_id = data['chat_id']
    message_id = data['message_id']
    if not chat_id or not message_id:
        raise FileReferenceExpiredError(None)

    logger.info(
        "Refreshing file_reference for %s id=%d from chat=%d msg=%d",
        data['type'], data['id'], chat_id, message_id
    )
    msgs = await client.get_messages(chat_id, ids=message_id)
    if not msgs or not msgs.media:
        raise ValueError(
            f"Could not re-fetch message {message_id} in chat {chat_id}"
        )

    _, obj = _extract_media(msgs)
    if obj.id != data['id']:
        raise ValueError(
            f"Re-fetched media id={obj.id} doesn't match packed id={data['id']}"
        )

    data['file_reference'] = obj.file_reference
    return data


async def send_media(client, entity, media_id_str, *,
                     caption=None, parse_mode=None, buttons=None,
                     reply_to=None, **kwargs):
    """Send media from a packed media ID string.

    On FileReferenceExpiredError, automatically refreshes the reference
    using the stored chat_id/message_id and retries.

    Args:
        client: TelegramClient instance.
        entity: Target chat/user to send to.
        media_id_str: Packed media ID string from pack_media_id().
        caption: Message caption text.
        parse_mode: Parse mode for caption ('html', 'md', etc.).
        buttons: Inline buttons.
        reply_to: Message to reply to.
        **kwargs: Additional arguments passed to client.send_file().

    Returns:
        (Message, media_id_str) — the sent message and the (possibly
        updated) media ID string. If the file_reference was refreshed,
        the returned string will differ from the input — persist it.
    """
    input_media = to_input_media(media_id_str)
    send_kwargs = dict(
        caption=caption, parse_mode=parse_mode,
        buttons=buttons, reply_to=reply_to,
        **kwargs
    )

    try:
        msg = await client.send_file(entity, input_media, **send_kwargs)
        return msg, media_id_str
    except FileReferenceExpiredError:
        logger.info("File reference expired, attempting refresh")

    data = unpack_media_id(media_id_str)
    data = await _refresh_file_reference(client, data)
    updated_id = _repack(data)
    input_media = to_input_media(updated_id)
    msg = await client.send_file(entity, input_media, **send_kwargs)
    return msg, updated_id
