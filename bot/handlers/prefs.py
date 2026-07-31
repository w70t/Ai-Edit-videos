"""
⚙️ الإعدادات — the settings panel.

Everything the admin used to change by SSH-ing into the Pi, editing `.env` and
restarting the service is now a button here. Each tap writes the new value to
PREFS_DB immediately (see services/prefs.py), so it holds across restarts.

Only genuinely runtime-changeable settings appear. A button that claimed to
change the bot token or the worker count would be lying until the next restart,
so those stay in `.env` and the panel says so.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.types import CallbackQuery, Message

from ..filters import IsAdmin
from ..keyboards import (BTN_SETTINGS, INTENSITY_BUTTON,
                         confirm_forget_keyboard, prefs_keyboard)
from ..services.dedup import registry
from ..services.prefs import prefs

router = Router(name="prefs")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _panel_text() -> str:
    """The panel explains each switch, so the buttons never need guessing."""
    ask = "✅ مفعّل" if prefs.confirm_before_edit else "⛔ متوقف"
    dedup = "✅ مفعّل" if prefs.skip_duplicates else "⛔ متوقف"
    intensity = INTENSITY_BUTTON.get(prefs.default_intensity,
                                     prefs.default_intensity)
    return "\n".join([
        "⚙️ الإعدادات",
        "",
        "كل ما تحت يتغيّر بضغطة زر ويُحفظ فوراً — بدون تعديل ملف .env "
        "وبدون إعادة تشغيل البوت.",
        "",
        f"🛑 يسأل قبل التعديل: {ask}",
        "   مفعّل: يوصل الفيديو ويسألك الشدّة قبل أي معالجة.",
        "   متوقف: يعدّل مباشرة بالوضع الافتراضي تحت.",
        "",
        f"⏭️ تخطّي الفيديو المكرر: {dedup}   (المسجّل: {registry.count()} بصمة)",
        "   يتجاهل أي فيديو سبق أن عالجته بنفس الملف.",
        "",
        f"🎚 الوضع الافتراضي: {intensity}",
        "   يُستخدم مباشرة عند إيقاف السؤال، وهو المقترح في شاشة التأكيد.",
        "",
        "تبقى في ملف .env (وتحتاج إعادة تشغيل): التوكن، ADMIN_ID، قناة "
        "الإرسال، عدد المهام المتزامنة، حدود الحجم، التسريع العتادي، والمجلدات.",
    ])


def _panel_markup():
    return prefs_keyboard(prefs.confirm_before_edit, prefs.skip_duplicates,
                          prefs.default_intensity)


async def _refresh(cq: CallbackQuery) -> None:
    """Redraw the panel in place after a change."""
    try:
        await cq.message.edit_text(_panel_text(), reply_markup=_panel_markup())
    except Exception:
        pass


@router.message(or_f(Command("settings"), F.text == BTN_SETTINGS))
async def cmd_prefs(message: Message) -> None:
    await message.answer(_panel_text(), reply_markup=_panel_markup())


@router.callback_query(F.data.startswith("pref:tog:"))
async def cb_pref_toggle(cq: CallbackQuery) -> None:
    field = cq.data.split(":")[2]
    value = prefs.toggle(field)
    names = {"confirm_before_edit": "السؤال قبل التعديل",
             "skip_duplicates": "تخطّي المكرر"}
    await cq.answer(f"{names.get(field, field)} → "
                    f"{'مفعّل ✅' if value else 'متوقف ⛔'}")
    await _refresh(cq)


@router.callback_query(F.data.startswith("pref:int:"))
async def cb_pref_intensity(cq: CallbackQuery) -> None:
    value = cq.data.split(":")[2]
    if not prefs.set_intensity(value):
        await cq.answer()
        return
    await cq.answer(f"الوضع الافتراضي → {INTENSITY_BUTTON.get(value, value)}")
    await _refresh(cq)


@router.callback_query(F.data == "pref:forget")
async def cb_pref_forget(cq: CallbackQuery) -> None:
    """Same wipe as the menu button, reached from the panel — still confirmed."""
    count = registry.count()
    if not count:
        await cq.answer("السجل فارغ أصلاً — لا شيء لمسحه.", show_alert=True)
        return
    await cq.message.edit_text(
        f"⚠️ سيُمسح سجل تخطّي المكرر ({count} بصمة).\n"
        f"أي فيديو سبق إرساله سيُعالَج من جديد. متأكد؟",
        reply_markup=confirm_forget_keyboard())
    await cq.answer()


@router.callback_query(F.data == "pref:close")
async def cb_pref_close(cq: CallbackQuery) -> None:
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()
