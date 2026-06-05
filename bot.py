import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ─── НАСТРОЙКИ ────────────────────────────────────────────────
BOT_TOKEN = "8943555644:AAGK8SKCTzIPozEE5dav0xUGU_b1hJKEfRk"
API_KEY   = "wk_live_PzMSMHecMZ65_d9STH9VnJaETfv8hhsHu20Gd6E1xts"
BASE_URL  = "https://n.santehstroyminsk.org/api/v1/worker-api"

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
}

# ─── ЛОГИРОВАНИЕ ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─── СОСТОЯНИЯ FSM ────────────────────────────────────────────
class ActivationStates(StatesGroup):
    waiting_phone  = State()
    waiting_code   = State()

# ─── БОТ И ДИСПЕТЧЕР ─────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ─── ХРАНИЛИЩЕ АКТИВНЫХ СЕССИЙ ────────────────────────────────
# { user_id: activation_id }
user_activations: dict[int, str] = {}

# ─── КНОПКА «ОТМЕНА» ──────────────────────────────────────────
cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True,
)

# ─── /start ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Я бот для SMS-активации.\n\n"
        "Отправь мне номер телефона в формате <b>+79001234567</b>",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    await state.set_state(ActivationStates.waiting_phone)


# ─── ОТМЕНА ───────────────────────────────────────────────────
@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    uid = message.from_user.id
    # пробуем отменить активацию на сайте
    act_id = user_activations.pop(uid, None)
    if act_id:
        await cancel_activation(act_id)

    await state.clear()
    await message.answer(
        "Операция отменена. Введи /start чтобы начать заново.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ─── ШАГ 1: ПОЛУЧАЕМ НОМЕР ────────────────────────────────────
@dp.message(ActivationStates.waiting_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()

    # простая валидация
    if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 10:
        await message.answer(
            "⚠️ Неверный формат. Отправь номер в виде <b>+79001234567</b>",
            parse_mode="HTML",
        )
        return

    await message.answer("⏳ Отправляю номер на сервис...")

    activation_id, error = await create_activation(phone)

    if error:
        await message.answer(f"❌ Ошибка: {error}\n\nПопробуй другой номер.")
        return

    user_activations[message.from_user.id] = activation_id

    await state.update_data(phone=phone, activation_id=activation_id)
    await state.set_state(ActivationStates.waiting_code)

    await message.answer(
        f"✅ Номер <b>{phone}</b> принят!\n\n"
        f"📱 Ожидай SMS-код и отправь его сюда.",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )


# ─── ШАГ 2: ПОЛУЧАЕМ КОД ─────────────────────────────────────
@dp.message(ActivationStates.waiting_code)
async def process_code(message: Message, state: FSMContext):
    code = message.text.strip()

    if not code.isdigit():
        await message.answer("⚠️ Код должен состоять только из цифр. Попробуй ещё раз.")
        return

    data = await state.get_data()
    activation_id = data.get("activation_id")

    await message.answer("⏳ Передаю код на сервис...")

    success, error = await submit_code(activation_id, code)

    if error:
        await message.answer(f"❌ Ошибка: {error}\n\nПопробуй ввести код ещё раз.")
        return

    user_activations.pop(message.from_user.id, None)
    await state.clear()

    await message.answer(
        f"🎉 Готово! Код <b>{code}</b> успешно передан.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Введи /start чтобы начать новую активацию.")


# ─── API: СОЗДАТЬ АКТИВАЦИЮ ───────────────────────────────────
async def create_activation(phone: str) -> tuple[str | None, str | None]:
    """
    POST /activations  { "phone": "+79001234567" }
    Возвращает (activation_id, None) или (None, error_message)
    """
    url = f"{BASE_URL}/activations"
    payload = {"phone": phone}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                logger.info("create_activation response %s: %s", resp.status, data)

                if resp.status in (200, 201):
                    # пробуем разные варианты поля с id
                    act_id = (
                        data.get("id")
                        or data.get("activation_id")
                        or data.get("data", {}).get("id")
                    )
                    if act_id:
                        return str(act_id), None
                    return None, f"Не удалось получить ID активации. Ответ: {data}"

                error_msg = data.get("message") or data.get("error") or str(data)
                return None, error_msg

    except aiohttp.ClientError as e:
        logger.error("create_activation network error: %s", e)
        return None, f"Сетевая ошибка: {e}"


# ─── API: ПЕРЕДАТЬ КОД ────────────────────────────────────────
async def submit_code(activation_id: str, code: str) -> tuple[bool, str | None]:
    """
    POST /activations/{id}/code  { "code": "1234" }
    или PATCH /activations/{id}  { "code": "1234" }
    """
    url = f"{BASE_URL}/activations/{activation_id}/code"
    payload = {"code": code}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                logger.info("submit_code response %s: %s", resp.status, data)

                if resp.status in (200, 201, 204):
                    return True, None

                # если POST /code не работает — пробуем PATCH
                if resp.status == 404:
                    return await submit_code_patch(activation_id, code)

                error_msg = data.get("message") or data.get("error") or str(data)
                return False, error_msg

    except aiohttp.ClientError as e:
        logger.error("submit_code network error: %s", e)
        return False, f"Сетевая ошибка: {e}"


async def submit_code_patch(activation_id: str, code: str) -> tuple[bool, str | None]:
    url = f"{BASE_URL}/activations/{activation_id}"
    payload = {"code": code, "status": "SUCCESS"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, json=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                logger.info("submit_code_patch response %s: %s", resp.status, data)

                if resp.status in (200, 201, 204):
                    return True, None

                error_msg = data.get("message") or data.get("error") or str(data)
                return False, error_msg

    except aiohttp.ClientError as e:
        return False, f"Сетевая ошибка: {e}"


# ─── API: ОТМЕНИТЬ АКТИВАЦИЮ ──────────────────────────────────
async def cancel_activation(activation_id: str):
    url = f"{BASE_URL}/activations/{activation_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                url,
                json={"status": "CANCEL"},
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                logger.info("cancel_activation %s → %s", activation_id, resp.status)
    except Exception as e:
        logger.warning("cancel_activation error: %s", e)


# ─── ЗАПУСК ───────────────────────────────────────────────────
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
