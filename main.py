import os
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import psycopg2
from dotenv import load_dotenv
from openai import OpenAI
from datetime import datetime
import pytz
import json
import asyncio

load_dotenv()
USUARIOS_INICIADOS = set()

#----------------------------------------------------------------------------------------------------------
# Variables de entorno
#----------------------------------------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USERS = os.getenv("ALLOWED_USERS")

client = OpenAI(
    base_url=os.getenv("BASE_URL"),
    api_key=os.getenv("API_KEY_IA")
)


def usuario_permitido(user_id: int) -> bool:
    allowed_raw = ALLOWED_USERS or ""
    allowed_ids = [item.strip() for item in allowed_raw.split(",") if item.strip()]
    return str(user_id) in allowed_ids

#----------------------------------------------------------------------------------------------------------
# Conexion a DB.
#----------------------------------------------------------------------------------------------------------
conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    database=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASS"),
    sslmode=os.getenv("DB_SSLMODE")
)
cursor = conn.cursor()  # instancia la conexion a la DB.

#----------------------------------------------------------------------------------------------------------
# Funcion para generar la respuesta de la IA en base al mensaje recibido de telegram.
#----------------------------------------------------------------------------------------------------------
path_prompt = os.getenv("SYSTEM_PROMPT")
with open(path_prompt, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()


def generar_sql(message_user: str, user: int) -> str:
    local_tz = pytz.timezone("America/Argentina/Buenos_Aires")
    resp = client.chat.completions.create(
        model=os.getenv("MODEL_IA"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"La fecha y hora actual es {datetime.now(local_tz)}. "
                    f"La Zona horaria es GMT-3 (Argentina). "
                    f"Usuario: {user} - Mensaje: {message_user}"
                ),
            },
        ],
    )
    return resp.choices[0].message.content.strip()


#----------------------------------------------------------------------------------------------------------
# Funcion para validar si el sql generado por la IA es una de las operaciones permitidas.
#----------------------------------------------------------------------------------------------------------
def sql_permitido(sql):
    if not sql:
        return False

    permitido = ["insert", "update", "delete", "select"]
    inicio = sql.lower().split()[0]

    return inicio in permitido

#----------------------------------------------------------------------------------------------------------
# Funcion para escuchar lo que se recibe desde telegram.
#----------------------------------------------------------------------------------------------------------
# @router.message(Command("start"))
async def init_app(message: Message):
    if not usuario_permitido(message.from_user.id):
        return

    USUARIOS_INICIADOS.add(message.from_user.id)
    await message.answer(
        "ðŸ‘‹ Hola, soy tu bot de finanzas personales.\n\n" +
        "Puedo ayudarte a gestionar tus movimientos de dinero de forma simple.\n\n" +
        "Podés escribirme en lenguaje natural para:\n" +
        "- Registrar ingresos y gastos.\n" +
        "- Editar o eliminar movimientos.\n" +
        "- Consultar saldos, totales y reportes.\n\n" +
        "Solo tenés que indicarme:\n" +
        "- La fecha del movimiento.\n" +
        "- El importe.\n" +
        "- Forma de pago.\n" +
        "- El motivo o concepto.\n\n" +
        "Por ejemplo:\n" +
        "\"Ayer gasté $1000 en combustible y pague en efectivo\"\n\n" +
        "Voy a interpretar tu mensaje y realizar la operación correspondiente según lo que indiques.\n\n" +
        "Cuando quieras, mandame tu primera operación 💸¸"
    )


async def procesar_mensaje(message: Message):
    print(message.from_user)
    print(f'{'Usuario: ' + str(message.from_user.id) + ' - Mensaje: '+ message.text}')

    texto = (message.text or "").strip()
    if texto.startswith("/"):
        await init_app(message)
        return


    if message.from_user.id not in USUARIOS_INICIADOS:
        await message.answer("Primero ejecuta /start para iniciar el bot.")
        return

    if not usuario_permitido(message.from_user.id):
        return

    try:

        texto = message.text  # Mensaje recibido de telegram
        data = generar_sql(texto, message.from_user.id)  # Genera la respuesta de la IA en base al mensaje recibido.
        
        f_data = json.loads(data)  # Parseo a JSON

        # Obtenemos los datos de los campos del JSON
        action = f_data.get("action", "")
        sql_op = f_data.get("sql", "")
        message_op = f_data.get("message", "")
        tipo = f_data.get("tipo", "")
        importe = f_data.get("importe", 0)
        concepto = f_data.get("concepto", "")
        fecha = f_data.get("fecha", "")
        forma_pago = f_data.get("forma", "")

        if not sql_permitido(sql_op):
            return  # Si no es un sql permitido, no hace nada.

        print(f"SQL generado: {sql_op}")

        cursor.execute(sql_op)

        if action.lower().startswith("select"):
            filas = cursor.fetchall()

            if len(filas) == 0:
                await message.answer("Sin resultados.")
                return

            if len(filas) == 1 and len(filas[0]) == 1:
                valor = filas[0][0]
                await message.answer(f"{message_op}: ${valor}")
                return

            resp = ""
            for fila in filas:
                resp += " | ".join(str(c) for c in fila) + "\n"

            await message.answer(resp)
        else:
            conn.commit()
            if sql_op.lower().startswith("insert"):
                message_bot = (
                    f"{"🔴" if tipo.lower() == 'debito' else "🟢"} {message_op}\n\n"
                    f"📝 Concepto: {concepto}\n"
                    f"💰° Importe: ${importe}\n"
                    f"📅 Fecha: {fecha}\n"
                    f"🔖 Tipo: {tipo}\n"
                    f"💳 Forma de pago: {forma_pago}\n"
                )

                await message.answer(message_bot)
            elif sql_op.lower().startswith("update"):
                await message.answer("Movimiento actualizado.")
            elif sql_op.lower().startswith("delete"):
                await message.answer("Movimiento eliminado.")

    except Exception as e:
        conn.rollback()
        await message.answer(f"Error:\n{str(e)}")


#----------------------------------------------------------------------------------------------------------
# Funcion MAIN
#----------------------------------------------------------------------------------------------------------
async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )
    dp = Dispatcher()    
    dp.message.register(procesar_mensaje, F.text)

    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())