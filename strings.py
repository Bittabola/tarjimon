"""
Centralized strings file for Tarjimon Bot.
All user-facing strings in Uzbek.

To update translations, simply edit the strings in this file.
"""

from typing import Final


# =============================================================================
# RATE LIMIT ERRORS
# =============================================================================

DAILY_TOKEN_LIMIT_EXCEEDED: Final[str] = """Kunlik limit tugadi

Siz bir kun uchun ajratilgan barcha limitni ishlatib bo'ldingiz. Yangi limitlar ertaga taqdim etiladi.

Kunlik statistika:
Ishlatildi: {used:,} birlik
Limit: {limit:,} birlik
Qoldi: {remaining:,} birlik

Xizmatdan foydalanishni ertaga yangi limitlar bilan davom ettirishingiz mumkin. Sabringiz uchun rahmat!"""

TOO_MANY_REQUESTS: Final[str] = """Juda ko'p so'rov yuborildi

Siz qisqa vaqt ichida botga haddan tashqari ko'p so'rov yubordingiz. Iltimos, biroz sekinroq.

So'rovlar statistikasi:
So'nggi daqiqada: {recent} ta so'rov
Ruxsat etilgan: {limit} ta so'rov (daqiqasiga)

Iltimos, bir necha soniya kuting va qaytadan urinib ko'ring."""

MONTHLY_SERVICE_LIMIT: Final[str] = """{service} uchun oylik limit tugadi

Ushbu oyda {service} xizmati uchun ajratilgan limit to'liq sarflandi. Afsuski, hozircha yangi so'rovlarni qabul qila olmaymiz.

Oylik statistika:
Ishlatildi: {used:,} birlik
Limit: {limit:,} birlik
Qoldi: {remaining:,} birlik

Limitlar keyingi oyda yangilanadi. Agar shoshilinch savollaringiz bo'lsa, administrator bilan bog'laning."""

MONTHLY_SYSTEM_LIMIT: Final[str] = """Botning umumiy oylik limiti tugadi

Ushbu oyda bot uchun ajratilgan umumiy limit to'liq sarflandi. Afsuski, hozircha yangi so'rovlarni qabul qila olmaymiz.

Umumiy statistika:
Ishlatildi: {used:,} birlik
Limit: {limit:,} birlik
Qoldi: {remaining:,} birlik

Limitlar keyingi oyda yangilanadi. Agar shoshilinch savollaringiz bo'lsa, administrator bilan bog'laning."""


# =============================================================================
# INPUT VALIDATION ERRORS
# =============================================================================

TEXT_TOO_LONG: Final[str] = """Yuborilgan matn juda uzun

Siz yuborgan matn hajmi ruxsat etilganidan oshib ketdi. Iltimos, matnni qisqartiring va qayta yuboring.

Matn hajmi:
Sizning matningiz: {actual:,} ta belgi
Ruxsat etilgan: {limit:,} ta belgi

Matnni bo'laklarga ajratish yoki qisqartirish orqali bu muammoni hal qilishingiz mumkin."""

IMAGE_TOO_LARGE: Final[str] = "Rasm hajmi juda katta! Maksimal hajm: {max_size}MB."

EMPTY_TEXT: Final[str] = "Matn bo'sh bo'lishi mumkin emas."

TEXT_TOO_SHORT: Final[str] = (
    "Matn juda qisqa. Kamida {min_length} ta belgi bo'lishi kerak."
)

NO_CONTENT: Final[str] = "Tarjima uchun matn yoki rasm topilmadi."

SEND_TEXT_OR_IMAGE: Final[str] = "Iltimos, matnli xabar yoki rasm yuboring."


# =============================================================================
# SUBSCRIPTION ERRORS
# =============================================================================

TRANSLATION_LIMIT_EXCEEDED_FREE: Final[str] = """Tarjima limiti tugadi.

Bepul limit: oyiga {free_limit} ta.

Premium paket ({stars} Yulduz):
- {translation_limit} ta tarjima
- {days} kun amal qiladi"""

TRANSLATION_LIMIT_EXCEEDED_PREMIUM: Final[str] = """Tarjima limiti tugadi.

Premium paket ({stars} Yulduz):
- {translation_limit} ta tarjima
- {days} kun amal qiladi"""

INVALID_PLAN: Final[str] = "Noto'g'ri obuna turi."

PAYMENT_ALREADY_PROCESSED: Final[str] = (
    "To'lov allaqachon amalga oshirilgan. Obunangiz faol."
)

PAYMENT_LOG_ERROR: Final[str] = (
    "To'lov qabul qilindi, ammo qayta ishlashda xatolik yuz berdi. Iltimos, administrator bilan bog'laning."
)

ACTIVATION_ERROR: Final[str] = (
    "To'lov qabul qilindi, ammo obunani faollashtirishda xatolik yuz berdi. Iltimos, administrator bilan bog'laning."
)


# =============================================================================
# GENERAL MESSAGES
# =============================================================================

GENERIC_ERROR: Final[str] = "Xatolik yuz berdi."

ERROR_MODEL_OVERLOADED: Final[str] = (
    "Hozirda serverda yuklanish yuqori. Iltimos, bir necha daqiqadan keyin qayta urinib ko'ring."
)

ERROR_TIMED_OUT: Final[str] = (
    "So'rov vaqti tugadi. Iltimos, qayta urinib ko'ring yoki qisqaroq matn yuboring."
)

ERROR_SERVICE_UNAVAILABLE: Final[str] = (
    "Xizmat vaqtincha mavjud emas. Iltimos, keyinroq qayta urinib ko'ring."
)

ERROR_CLIENT_REQUEST: Final[str] = (
    "So'rovni qayta ishlashda xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."
)

PROCESSING: Final[str] = "Xabar qabul qilindi. Tekshirilmoqda..."

TRANSLATING: Final[str] = "Tarjima qilinmoqda..."

INVALID_CALLBACK_DATA: Final[str] = "Xatolik: noto'g'ri ma'lumot."


# =============================================================================
# TRANSLATION MESSAGES
# =============================================================================

OCR_NO_TEXT: Final[str] = "Rasmda matn topilmadi."

ALREADY_UZBEK: Final[str] = "Matn allaqachon o'zbek tilida."

TRANSLATION_FAILED: Final[str] = "Tarjima jarayonida xatolik yuz berdi."


# =============================================================================
# FORMATTING LABELS (with HTML tags)
# =============================================================================

LABEL_IMAGE_RESULT: Final[str] = "📷 <b>Rasm natijasi:</b>\n"
LABEL_IMAGE_TRANSLATION: Final[str] = "📷 <b>Rasm tarjimasi:</b>\n"
LABEL_IMAGE_AND_TEXT_TRANSLATION: Final[str] = "📷📝 <b>Rasm va matn tarjimasi:</b>\n\n"
LABEL_TEXT_RESULT: Final[str] = "📝 <b>Matn natijasi:</b>\n"
LABEL_TEXT_TRANSLATION: Final[str] = "📝 <b>Matn tarjimasi:</b>\n"
LABEL_TRANSLATION: Final[str] = "<b>Tarjima:</b>\n\n"


# =============================================================================
# BUTTON LABELS
# =============================================================================

BTN_STATS: Final[str] = "Statistika"
BTN_SUBSCRIBE: Final[str] = "Obuna bo'lish"
BTN_INCREASE_LIMIT: Final[str] = "Limitni oshirish"


# =============================================================================
# PAYMENT MESSAGES
# =============================================================================

PAYMENT_SUCCESS_TITLE: Final[str] = "<b>To'lov muvaffaqiyatli amalga oshirildi!</b>\n\n"
PAYMENT_SUBSCRIPTION_ACTIVATED: Final[str] = "Premium obuna faollashtirildi.\n"
PAYMENT_EXPIRES_AT: Final[str] = "Amal qilish muddati: <b>{date}</b> gacha\n\n"
PAYMENT_YOUR_LIMITS: Final[str] = "<b>Sizning limitlaringiz:</b>\n"
PAYMENT_TRANSLATIONS_FORMAT: Final[str] = "- {count} ta tarjima\n\n"
PAYMENT_THANK_YOU: Final[str] = "Xizmatdan foydalanganingiz uchun rahmat!"


# =============================================================================
# START COMMAND
# =============================================================================

WELCOME_MESSAGE: Final[str] = """Salom! Men sizga quyidagi xizmatlarni taklif qilaman:

<b>Tarjima:</b> Matn, rasm yoki forward qilingan xabarlarni o'zbekchaga o'girish

{status_text}

<b>Bepul reja:</b> {free_translations} ta tarjima (30 kun)
<b>Premium:</b> {premium_translations} ta tarjima (30 kun)

<i>Xizmat Google serverlaridagi Gemini modeli asosida ishlaydi.</i>"""

STATUS_PREMIUM: Final[str] = """<b>Premium obuna:</b> {date} gacha
Qolgan limitlar: {translations} ta tarjima"""

STATUS_FREE: Final[str] = """<b>Bepul reja</b>
Qolgan limitlar: {translations} ta tarjima"""


# =============================================================================
# SUBSCRIBE COMMAND
# =============================================================================

SUBSCRIBE_HEADING: Final[str] = "<b>Premiumga o'tish</b>"

SUBSCRIBE_PREMIUM_USER_INFO: Final[str] = """<b>Sizda premium obuna mavjud!</b>

Amal qilish muddati: {days_remaining} kun (<b>{date}</b> gacha)
Qolgan limitlar: {translations} ta tarjima

Limitlarni oshirish uchun yangi paket xarid qilishingiz mumkin:"""

SUBSCRIBE_FREE_USER_INFO: Final[str] = """<b>Bepul reja:</b>
- oyiga {free_translations} ta tarjima

<b>Premium paket ({stars} Yulduz):</b>
- {premium_translations} ta tarjima
- {days} kun amal qiladi"""


# =============================================================================
# STATS MESSAGES
# =============================================================================

STATS_PREMIUM: Final[str] = """<b>Premium obuna</b>

Amal qilish muddati: {days_remaining} kun (<b>{date}</b> gacha)

<b>Qolgan limitlar:</b>
- {translations} ta tarjima

Limitlarni oshirish uchun yangi paket xarid qiling:"""

STATS_FREE: Final[str] = """<b>Bepul reja</b>

<b>Qolgan limitlar (oylik):</b>
- {translations}/{free_translations} ta tarjima

<b>Premium paket ({stars} Yulduz):</b>
- {premium_translations} ta tarjima
- {days} kun amal qiladi"""


# =============================================================================
# SUBSCRIPTION PLAN
# =============================================================================

PLAN_TITLE: Final[str] = "Premium paket"

PLAN_DESCRIPTION: Final[str] = (
    "{translations} ta tarjima, {days} kun"
)


# =============================================================================
# MONTH NAMES
# =============================================================================

MONTHS: Final[list[str]] = [
    "yanvar",
    "fevral",
    "mart",
    "aprel",
    "may",
    "iyun",
    "iyul",
    "avgust",
    "sentabr",
    "oktabr",
    "noyabr",
    "dekabr",
]


# =============================================================================
# FEEDBACK MESSAGES
# =============================================================================

FEEDBACK_PROMPT: Final[str] = """Fikr-mulohaza yuborish uchun xabaringizni yozing.

Sizning fikringiz biz uchun muhim! Takliflar, shikoyatlar yoki savollaringizni yuboring."""

FEEDBACK_RECEIVED: Final[str] = """Fikr-mulohazangiz qabul qilindi!

Xabaringiz administratorga yuborildi. Javob bo'lsa, sizga xabar beramiz."""

FEEDBACK_SEND_ERROR: Final[str] = "Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

FEEDBACK_CANCELLED: Final[str] = "Fikr-mulohaza yuborish bekor qilindi."

FEEDBACK_REPLY_SENT: Final[str] = "Javobingiz foydalanuvchiga yuborildi."

FEEDBACK_REPLY_ERROR: Final[str] = "Javobni yuborishda xatolik yuz berdi."
