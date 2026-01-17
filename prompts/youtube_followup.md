# YouTube Follow-up Question Prompts
# These prompts are used for answering follow-up questions about YouTube videos.
# Variables: {question} - the user's question, {transcript} - the video transcript (only for with_transcript), {summary} - the video summary (only for with_summary)

---
## with_transcript
Savol: "{question}"

Quyida video transkripsiyasi berilgan:

TRANSKRIPSIYA:
{transcript}

Vazifa: Mazmuniga asoslanib, savolga qisqa va aniq javob bering.

Qoidalar:
- Faqat javobning o'zini yozing, kirish so'zlarsiz (masalan, "Men javob beraman" kabi iboralarni ishlatmang)
- "Transkripsiya", "video", "videoda aytilishicha", "transkriptda" kabi so'zlarni ishlatmang
- O'zbek tilida, lotin alifbosida yozing
- Qisqa va ixcham javob bering (3-5 jumla)
- Emoji va markdown ishlatmang
- Agar mazmunda javob bo'lmasa, qisqacha buni ayting

---
## without_transcript
Savol: "{question}"

Vazifa: Berilgan video mazmuniga asoslanib, savolga qisqa va aniq javob bering.

Qoidalar:
- Faqat javobning o'zini yozing, kirish so'zlarsiz (masalan, "Men javob beraman" kabi iboralarni ishlatmang)
- "Transkripsiya", "video", "videoda aytilishicha", "transkriptda" kabi so'zlarni ishlatmang
- O'zbek tilida, lotin alifbosida yozing
- Qisqa va ixcham javob bering (3-5 jumla)
- Emoji va markdown ishlatmang
- Agar mazmunda javob bo'lmasa, qisqacha buni ayting

---
## with_summary
Savol: "{question}"

Quyida video haqida ma'lumot berilgan:

{summary}

Vazifa: Yuqoridagi ma'lumotlarga asoslanib, savolga qisqa va aniq javob bering.

Qoidalar:
- Faqat javobning o'zini yozing, kirish so'zlarsiz (masalan, "Men javob beraman" kabi iboralarni ishlatmang)
- "Xulosa", "video", "videoda aytilishicha" kabi so'zlarni ishlatmang
- O'zbek tilida, lotin alifbosida yozing
- Qisqa va ixcham javob bering (3-5 jumla)
- Emoji va markdown ishlatmang
- Agar ma'lumotlarda javob bo'lmasa, shuni qisqacha ayting
