"""One self-contained HTML quiz widget for a threat.

Telegram runs the document in a QuickJS sandbox with no network, so everything
the quiz needs is baked in: three questions, four options, inline CSS/JS and a
per-session seed. Only correctness and the explanation come from the bot, via
``widget_query`` requests. Every string is escaped here; the sandbox carries no
external font, image or script.
"""

from __future__ import annotations

import json
from html import escape

from app.content.types import QuizView
from app.services.i18n import t

HTML_LIMIT = 32000
ZLIB_LIMIT = 4000
PROMPT_LIMIT = 300
OPTION_LIMIT = 160

_CSS = """
:root{--navy:#0b1f3a;--navy2:#132c4d;--amber:#f5b544;--teal:#24c2a5;--ink:#eaf1fb}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:transparent;font-family:system-ui,-apple-system,sans-serif}
.card{margin:6px;border-radius:14px;background:var(--navy);padding:12px;color:var(--ink)}
.top{display:flex;justify-content:space-between;font-size:11px;letter-spacing:.09em;color:var(--teal);text-transform:uppercase}
.bar{height:4px;border-radius:2px;background:#22405f;margin:8px 0 12px;overflow:hidden}
.fill{height:100%;width:0;background:var(--amber);transition:width .25s}
.q{font-size:15px;line-height:1.35;font-weight:600;margin:0 0 10px}
.opt{display:block;width:100%;text-align:left;margin:7px 0;padding:10px 11px;border-radius:10px;
 border:1px solid #2c4b6e;background:var(--navy2);color:var(--ink);font:inherit;font-size:14px;line-height:1.3}
.opt:active{background:#1b3a60}
.opt.ok{border-color:var(--teal);background:#0f3b3a}
.opt.bad{border-color:#e2604f;background:#3a1a1a}
.opt[disabled]{opacity:.9}
.fb{margin-top:10px;font-size:13px;line-height:1.4}
.fb b{color:var(--amber)}
.fb.ok b{color:var(--teal)}
.exp{color:#c9d8ea;margin-top:4px}
.act{margin-top:10px;padding:10px 12px;border:0;border-radius:10px;background:var(--amber);
 color:#1a1200;font:inherit;font-weight:700}
.fin{text-align:center;padding:6px 0}
.score{font-size:34px;font-weight:800;color:var(--amber)}
.muted{color:#9fb3ca;font-size:13px;margin-top:4px}
.note{color:#9fb3ca;font-size:12px;margin-top:8px}
"""

_JS = r"""
(function(){
var Q=JSON.parse('__Q__'),S='__S__',T='__T__',L=JSON.parse('__L__');
var i=0,busy=false,last='',W=typeof TelegramWidget==='undefined'?null:TelegramWidget;
var v=document.getElementById('v'),n=document.getElementById('n'),f=document.getElementById('f');
function node(t,c){var e=document.createElement(t);if(c)e.className=c;return e}
function draw(){
 busy=false;var q=Q[i];
 n.innerHTML=(i+1)+'/'+Q.length;f.style.width=((i+1)/Q.length*100)+'%';
 v.innerHTML='';
 var p=node('div','q');p.innerHTML=q.p;v.appendChild(p);
 for(var k=0;k<q.o.length;k++)(function(k){
  var b=node('button','opt');b.innerHTML=q.o[k];b.onclick=function(){ask(k,b)};v.appendChild(b);
 })(k);
}
function ask(k,btn){
 if(busy||!W)return;busy=true;
 var bs=v.querySelectorAll('.opt'),j;
 for(j=0;j<bs.length;j++)bs[j].disabled=true;
 var st=node('div','fb');st.innerHTML=L.checking;v.appendChild(st);
 W.request(JSON.stringify({s:S,q:Q[i].q,a:k,t:T})).then(function(r){
  var d=null;try{d=JSON.parse(r.result)}catch(e){}
  if(!d||!d.ok){fail(st);return}
  var ok=!!d.correct;btn.className=ok?'opt ok':'opt bad';
  st.className=ok?'fb ok':'fb';st.innerHTML='<b>'+(ok?L.correct:L.wrong)+'</b>';
  if(d.explanation){var x=node('div','exp');x.innerHTML=d.explanation;st.appendChild(x)}
  last=d.score||'';busy=false;
  var a=node('button','act');a.innerHTML=i+1<Q.length?L.next:L.finish;
  a.onclick=function(){if(i+1<Q.length){i++;draw()}else finish()};v.appendChild(a);
 },function(){fail(st)});
}
function fail(st){
 st.className='fb';st.innerHTML='<b>'+L.error+'</b>';busy=false;
 var r=node('button','act');r.innerHTML=L.retry;r.onclick=draw;v.appendChild(r);
}
function finish(){
 f.style.width='100%';v.innerHTML='';
 var p=(last||'').split('/'),c=parseInt(p[0],10)||0,t=parseInt(p[1],10)||Q.length;
 var box=node('div','fin');
 var s=node('div','score');s.innerHTML=last||'0/'+Q.length;box.appendChild(s);
 var m=node('div','muted');m.innerHTML=c>=t&&t>0?L.perfect:(c>0?L.partial:L.zero);box.appendChild(m);
 var r=node('button','act');r.innerHTML=L.restart;
 r.onclick=function(){i=0;last='';T=seed();draw()};box.appendChild(r);
 v.appendChild(box);
}
function seed(){var s=Math.floor(Math.random()*65536).toString(16);return('0000'+s).slice(-4)}
if(!W){document.getElementById('warn').hidden=false}
draw();
})();
"""


def _esc(value: object) -> str:
    return escape(str(value), quote=True)


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def query_data(slug: str, qid: str, answer: int, seed: str) -> str:
    """The 1-64 byte request body the widget sends for one tap."""
    return json.dumps(
        {"s": slug, "q": qid, "a": answer, "t": seed}, separators=(",", ":")
    )


def _labels(locale: str) -> dict[str, str]:
    return {
        "checking": _esc(t(locale, "game.checking")),
        "correct": _esc(t(locale, "game.correct")),
        "wrong": _esc(t(locale, "game.wrong")),
        "error": _esc(t(locale, "game.error")),
        "next": _esc(t(locale, "button.next_question")),
        "finish": _esc(t(locale, "game.finish")),
        "retry": _esc(t(locale, "button.retry")),
        "restart": _esc(t(locale, "button.retry")),
        "perfect": _esc(t(locale, "quiz.result.perfect")),
        "partial": _esc(t(locale, "quiz.result.partial")),
        "zero": _esc(t(locale, "quiz.result.zero")),
    }


def _embed(value: object) -> str:
    """JSON safe inside a single-quoted script literal and inside HTML."""
    raw = json.dumps(value, ensure_ascii=False)
    return raw.replace("<", "\\u003c").replace(">", "\\u003e")


def build_quiz(
    questions: list[QuizView], slug: str, seed: str, locale: str
) -> str:
    """Return the whole widget document for these questions."""
    data = [
        {
            "q": quiz.question_id,
            "p": _esc(_clip(quiz.prompt, PROMPT_LIMIT)),
            "o": [_esc(_clip(option, OPTION_LIMIT)) for option in quiz.options[:4]],
        }
        for quiz in questions
    ]
    labels = _labels(locale)
    script = (
        _JS.replace("__Q__", _embed(data))
        .replace("__S__", _esc(slug))
        .replace("__T__", _esc(seed))
        .replace("__L__", _embed(labels))
    )
    return (
        "<!doctype html><html lang=\""
        + _esc(locale)
        + "\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<style>{_CSS}</style></head><body>"
        "<div class=\"card\"><div class=\"top\"><span>"
        + _esc(t(locale, "game.title"))
        + "</span><span id=\"n\"></span></div>"
        "<div class=\"bar\"><div class=\"fill\" id=\"f\"></div></div>"
        "<div id=\"v\"></div><div class=\"note\" id=\"warn\" hidden>"
        + _esc(t(locale, "game.offline"))
        + "</div></div><script>"
        + script
        + "</script></body></html>"
    )


__all__ = ["HTML_LIMIT", "ZLIB_LIMIT", "build_quiz", "query_data"]
