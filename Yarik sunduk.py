from typing import Dict, Any, Optional, List, Union
from dataclasses import dataclass
import logging
from vkbottle import Bot, User, GroupEventType
from vkbottle.bot import BotLabeler

@dataclass
class ConfigType:
    BOT_TOKEN: str
    USER_TOKEN: str
    GROUP_ID: int
    SPECIAL_CHAT_ID: int
    LOG_LEVEL: int = logging.DEBUG

@dataclass
class UserInfo:
    id: int
    first_name: str
    last_name: str

class VKError(Exception):
    pass

CONFIG: ConfigType = ConfigType(
    BOT_TOKEN='',
    USER_TOKEN='',
    GROUP_ID=0,
    SPECIAL_CHAT_ID=0
)

logging.basicConfig(
    level=CONFIG.LOG_LEVEL,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

bot = Bot(token=CONFIG.BOT_TOKEN)
user = User(token=CONFIG.USER_TOKEN)
labeler = BotLabeler()

class VKHelper:
    @staticmethod
    async def fetch_user_info(user_id: int) -> Optional[UserInfo]:
        try:
            logger.debug(f"Запрос информации о пользователе ID: {user_id}")
            user_info = await bot.api.users.get(user_ids=user_id)

            if not user_info:
                return None

            user_data = user_info[0]
            return UserInfo(
                id=user_data.id, 
                first_name=user_data.first_name, 
                last_name=user_data.last_name
            )
        except Exception as e:
            logger.error(f"Ошибка получения информации о пользователе {user_id}: {e}")
            return None

    @staticmethod
    async def get_post_author(owner_id: int, post_id: int) -> int:
        try:
            logger.info(f"Попытка определить автора поста: owner_id={owner_id}, post_id={post_id}")

            posts = await user.api.wall.get(
                owner_id=owner_id,
                filter='all',
                count=1,
                item_ids=[post_id],
                extended=1
            )

            if not posts or not posts.items:
                logger.warning(f"Пост {owner_id}_{post_id} не найден")
                return owner_id

            post = posts.items[0]
            logger.info(f"Атрибуты поста: {vars(post)}")

            author_candidates: List[Optional[int]] = [
                getattr(post, 'created_by', None),
                getattr(post, 'signer_id', None),
                abs(getattr(post, 'from_id', 0)),
                abs(getattr(post, 'owner_id', 0)),
                abs(owner_id)
            ]

            author_candidates = [
                a for a in author_candidates 
                if a and a != 0
            ]

            if author_candidates:
                logger.info(f"Найденные кандидаты на авторство: {author_candidates}")
                return author_candidates[0]

            logger.warning(f"Не удалось определить автора поста {owner_id}_{post_id}")
            return owner_id

        except Exception as e:
            logger.error(f"Ошибка определения автора поста {owner_id}_{post_id}: {e}", exc_info=True)
            return owner_id
class NotificationService:
    @staticmethod
    async def send_special_chat(
        message: str, 
        payload: Optional[Dict[str, Union[str, int]]] = None
    ) -> None:
        try:
            if len(message) > 4096:
                message = message[:4096] + "... (сообщение обрезано)"

            await bot.api.messages.send(
                peer_id=CONFIG.SPECIAL_CHAT_ID, 
                message=message, 
                payload=payload or {}, 
                random_id=0
            )
            logger.info("Уведомление отправлено в специальный чат")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")

class EventHandlers:
    @labeler.raw_event(GroupEventType.WALL_POST_NEW)
    async def handle_wall_post(event: Dict[str, Any]) -> None:
        try:
            if not isinstance(event.get('object', {}), dict):
                logger.warning("Некорректный формат события")
                return

            post_id: Optional[int] = event['object'].get('id')
            owner_id: Optional[int] = event['object'].get('owner_id')

            if not post_id or not owner_id:
                logger.warning(f"Отсутствуют обязательные параметры: post_id={post_id}, owner_id={owner_id}")
                return

            post_type: str = event['object'].get('post_type', 'default')
            from_id: Optional[int] = event['object'].get('from_id')
            created_by: int = event['object'].get('created_by', from_id or owner_id)

            admin = await VKHelper.fetch_user_info(created_by)
            if not admin:
                logger.warning(f"Не удалось получить информацию о пользователе {created_by}")
                return

            message: str = (
                f"🔔 Новый предложенный пост! 🤔\n"
                f"📜 ID записи: {post_id}\n"
                f"👤 Предложил: {admin.first_name} {admin.last_name}\n"
                f"🔗 Ссылка: https://vk.com/wall{owner_id}_{post_id}"
            ) if post_type == 'suggest' else (
                f"🔔 Новая запись на стене сообщества! 🔔\n"
                f"📜 ID записи: {post_id}\n"
                f"👤 Администратор: {admin.first_name} {admin.last_name}\n"
                f"🔗 Ссылка: https://vk.com/wall{owner_id}_{post_id}"
            )

            payload_type = "suggested_post" if post_type == 'suggest' else "new_wall_post"

            await NotificationService.send_special_chat(
                message, 
                {
                    'type': "post", 
                    'key': payload_type,
                    'post_id': post_id,
                    'owner_id': owner_id
                }
            )

        except Exception as e:
            logger.error(f"Критическая ошибка обработки новой записи: {e}", exc_info=True)

    @labeler.raw_event(GroupEventType.LIKE_ADD)
    async def handle_like(event: Dict[str, Any]) -> None:
        try:
            owner_id: int = event['object']['object_owner_id']
            post_id: int = event['object']['object_id']
            liker_id: int = event['object']['liker_id']

            liker = await VKHelper.fetch_user_info(liker_id)
            if not liker:
                logger.warning(f"Не удалось получить информацию о пользователе {liker_id}")
                return

            post_author_id = await VKHelper.get_post_author(owner_id, post_id)

            message_parts: List[str] = [
                "❤️ Новый Лайк! 💕",
                f"📜 Информация о записи:",
                f"🆔 ID Записи: {post_id}",
                f"👤 Лайкнул: {liker.first_name} {liker.last_name} (ID: {liker.id})"
            ]

            if post_author_id:
                post_author = await VKHelper.fetch_user_info(post_author_id)
                if post_author:
                    message_parts.append(
                        f"🖋 Автор записи: {post_author.first_name} {post_author.last_name} "
                        f"(ID: {post_author.id})"
                    )
                else:
                    message_parts.append("🖋 Автор записи: Не удалось определить")
            else:
                message_parts.append("🖋 Автор записи: Не определен")

            message_parts.append(f"🔗 Ссылка: https://vk.com/wall{owner_id}_{post_id}")

            message = "\n".join(message_parts)
            await NotificationService.send_special_chat(
                message, 
                {
                    'type': "like", 
                    'key': "new_like",
                    'post_id': post_id,
                    'liker_id': liker_id
                }
            )

        except Exception as e:            
            logger.error(f"Критическая ошибка обработки лайка: {e}", exc_info=True)

    @labeler.raw_event(GroupEventType.LIKE_REMOVE)
    async def handle_like_remove(event: Dict[str, Any]) -> None:
        try:
            owner_id: int = event['object']['object_owner_id']
            post_id: int = event['object']['object_id']
            liker_id: int = event['object']['liker_id']

            liker = await VKHelper.fetch_user_info(liker_id)
            if not liker:
                logger.warning(f"Не удалось получить информацию о пользователе {liker_id}")
                return

            post_author_id = await VKHelper.get_post_author(owner_id, post_id)

            message_parts: List[str] = [
                "❌ Лайк удален! 💔",
                f"📜 Информация о записи:",
                f"🆔 ID Записи: {post_id}",
                f"👤 Убрал лайк: {liker.first_name} {liker.last_name} (ID: {liker.id})"
            ]

            if post_author_id:
                post_author = await VKHelper.fetch_user_info(post_author_id)
                if post_author:
                    message_parts.append(
                        f"🖋 Автор записи: {post_author.first_name} {post_author.last_name} "
                        f"(ID: {post_author.id})"
                    )
                else:
                    message_parts.append("🖋 Автор записи: Не удалось определить")
            else:
                message_parts.append("🖋 Автор записи: Не определен")

            message_parts.append(f"🔗 Ссылка: https://vk.com/wall{owner_id}_{post_id}")

            message = "\n".join(message_parts)
            await NotificationService.send_special_chat(
                message, 
                {
                    'type': "like_remove", 
                    'key': "like_removed",
                    'post_id': post_id,
                    'liker_id': liker_id
                }
            )

        except Exception as e:            
            logger.error(f"Критическая ошибка обработки удаления лайка: {e}", exc_info=True)
bot.labeler.load(labeler)

def main() -> None:
    try:
        logger.info("🤖 Бот VK запускается...")
        bot.run_forever()
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")

if __name__ == "__main__":
    main()

