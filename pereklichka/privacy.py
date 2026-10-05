from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

PRIVACY_URL = "https://pereklichka.agentspore.com/privacy"
OPERATOR_NAME = "Roman Konnov"
OPERATOR_CONTACT = "https://t.me/exzentttt"
router = APIRouter()

DOCUMENT = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Какие данные хранит Перекличка и кому передаёт отчёты.">
<title>Политика конфиденциальности · Перекличка</title>
<style>
:root {color-scheme: light; --ink:#153c39; --muted:#48655f; --accent:#176d57;
  --paper:#f6f7f1; --line:#d7e1d6; --panel:#fff;}
* {box-sizing:border-box;}
body {margin:0; background:var(--paper); color:var(--ink);
  font:17px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif;}
a {color:var(--accent); text-underline-offset:.2em; overflow-wrap:anywhere;}
a:focus-visible {outline:3px solid var(--accent); outline-offset:5px; border-radius:3px;}
.wrap {width:min(100% - 40px, 1000px); margin:auto;}
header {padding:24px 0; border-bottom:1px solid var(--line);}
.header-row {display:flex; justify-content:space-between; align-items:center; gap:24px;}
.brand {font-weight:750; font-size:21px; text-decoration:none; color:var(--ink);}
.bot-link {font-size:14px; font-weight:650;}
main {padding:56px 0 40px;}
.eyebrow {font-size:13px; letter-spacing:.12em; text-transform:uppercase; color:var(--accent);}
h1 {font-size:clamp(32px,5vw,50px); line-height:1.12; letter-spacing:-.035em;
  max-width:780px; margin:12px 0 22px;}
.lead {font-size:20px; color:var(--muted); max-width:760px; margin:0 0 22px;}
.meta {font-size:14px; color:var(--muted);}
.layout {display:grid; grid-template-columns:220px minmax(0,1fr); gap:48px; margin-top:44px;}
nav {align-self:start; padding:18px 0; border-top:2px solid var(--accent);}
nav p {font-size:13px; font-weight:750; margin:0 0 14px;}
nav a {display:block; margin:0 0 12px; font-size:15px; text-decoration:none;}
article {min-width:0;}
section {scroll-margin-top:24px; padding:0 0 28px; margin:0 0 28px;
  border-bottom:1px solid var(--line);}
h2 {font-size:24px; line-height:1.3; letter-spacing:-.02em; margin:0 0 16px;}
p {margin:0 0 14px;}
ul {padding-left:22px; margin:0 0 16px;}
li {margin:0 0 10px;}
.operator {background:var(--panel); padding:24px; border-radius:18px;
  border:1px solid var(--line); overflow-wrap:anywhere;}
footer {border-top:1px solid var(--line); padding:24px 0 36px;
  font-size:14px; color:var(--muted);}
@media(max-width:700px) {main {padding-top:34px;} .layout {grid-template-columns:1fr;
  gap:24px; margin-top:30px;} nav {display:flex; flex-wrap:wrap; gap:8px 20px;}
  nav p {width:100%; margin-bottom:4px;} nav a {margin-bottom:4px;}
  .header-row {gap:12px;} .brand {font-size:19px;} .lead {font-size:18px;}}
</style>
</head>
<body>
<header><div class="wrap header-row">
<a class="brand" href="https://t.me/PereklichkaAppBot">Перекличка</a>
<a class="bot-link" href="https://t.me/PereklichkaAppBot">Открыть бота</a>
</div></header>
<main class="wrap">
<p class="eyebrow">О ваших данных</p>
<h1>Политика конфиденциальности</h1>
<p class="lead">Близкий отмечается через Алису. Перекличка передаёт ответы
родственникам в Telegram. Здесь описано, какие данные нужны для этого.</p>
<p class="meta">Обновлено 5 октября 2026 года</p>
<div class="layout">
<nav aria-label="Разделы политики"><p>Содержание</p>
<a href="#data">Какие данные храним</a><a href="#purpose">Зачем они нужны</a>
<a href="#access">Кто получает данные</a><a href="#consent">Согласие</a>
<a href="#storage">Хранение и удаление</a><a href="#operator">Контакт оператора</a>
</nav>
<article>
<section id="data"><h2>Какие данные храним</h2>
<ul>
<li>Идентификатор чата Telegram, принадлежность к семье и порядок оповещения родственников.</li>
<li>Имя близкого, время отметки, часовой пояс и задержку повторного оповещения.</li>
<li>Идентификатор привязанной колонки, дату согласия и версию текста согласия.</li>
<li>Ответы о самочувствии, отметку о приёме лекарств, просьбы,
время начала и завершения отметки.</li>
<li>Тексты уведомлений, их получателей, время отправки, попытки доставки и записи тревог.</li>
<li>Коды привязки со сроками действия и использования, записи неудачных попыток привязки,
а также хеши приглашений и сроки их действия.</li>
</ul>
<p>Перекличка получает текст распознанных ответов от Яндекса и не сохраняет аудиозаписи.
Черновик добавления или настройки близкого хранится в памяти до завершения,
отмены или перезапуска сервиса.</p></section>
<section id="purpose"><h2>Зачем они нужны</h2>
<p>Данные связывают колонку с нужной семьёй, помогают собрать отчёт об отметке
и предупредить родственников, если отметки нет вовремя. Служебные записи помогают
защитить привязку от подбора кода и повторить доставку при сбое.</p></section>
<section id="access"><h2>Кто получает данные</h2>
<p>Отчёты и тревоги направляются родственникам, присоединившимся к вашей семье
в боте. Приглашайте только тех, кому готовы передавать ответы близкого.</p>
<p>Оператор проекта имеет доступ к базе для обслуживания сервиса и обработки запросов.
Участники другой семьи не могут получать коды и менять настройки ваших близких.</p>
<p>Яндекс обрабатывает голосовой запрос и передаёт навыку распознанный текст.
Telegram обрабатывает переписку с ботом и доставляет сообщения родственникам.
Эти сервисы применяют собственные правила:
<a href="https://yandex.ru/legal/confidential/ru/">политика Яндекса</a> и
<a href="https://telegram.org/privacy">политика Telegram</a>.</p></section>
<section id="consent"><h2>Согласие на привязку</h2>
<p>Перед привязкой колонки Алиса рассказывает о передаче ответов родным и спрашивает
согласие. Привязка сохраняется после ответа «да». Дата и версия согласия записываются.
При ответе «нет» новая привязка не создаётся.
Семейные настройки, созданные родственником, остаются.</p></section>
<section id="storage"><h2>Хранение и удаление</h2>
<p>Записи хранятся в закрытой базе PostgreSQL на сервере проекта.
Общий срок хранения истории сейчас не задан; автоматическое удаление истории не настроено.</p>
<p>Код привязки действует 15 минут, приглашение действует сутки.
Истечение срока действия не означает автоматического удаления записи.</p>
<p>Чтобы запросить удаление данных или отозвать согласие, напишите оператору по контакту ниже.
Удаление проводится вручную; отдельной команды для него в боте пока нет.
Удаление записей проекта не удаляет уже отправленные сообщения из Telegram.</p></section>
<section id="operator" class="operator"><h2>Контакт оператора</h2>
<p><strong>Оператор:</strong> [[OPERATOR]]</p>
<p><strong>Контакт:</strong> <a href="[[CONTACT]]">@exzentttt в Telegram</a></p>
<p>Укажите, к какой семье относится запрос. Не присылайте токен бота, пароль
Telegram или код входа.</p></section>
</article></div></main>
<footer><div class="wrap">Перекличка ·
<a href="https://t.me/PereklichkaAppBot">Вернуться к боту</a></div></footer>
</body></html>
"""


def render_privacy() -> str:
    """Escape operator details before embedding them in the static document."""
    return DOCUMENT.replace("[[OPERATOR]]", escape(OPERATOR_NAME)).replace(
        "[[CONTACT]]", escape(OPERATOR_CONTACT)
    )


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy() -> HTMLResponse:
    """Serve a public policy without database queries or third-party resources."""
    return HTMLResponse(
        render_privacy(),
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
