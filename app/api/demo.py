# ruff: noqa: E501
"""A tiny single-file browser demo for two-user post and feed flows."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["demo"])

PAGE = r"""<!doctype html>
<html lang="ru">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Демо ленты</title>
<style>
body{font:16px system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 16px;color:#222}
section{border:1px solid #ddd;border-radius:10px;padding:16px;margin:16px 0}
label{display:block;margin:8px 0}input,button{font:inherit;padding:8px}input[type=email],input[type=password],input[type=text]{width:min(100%,420px)}
button{cursor:pointer;margin:6px 4px 6px 0}.row{display:flex;gap:24px;flex-wrap:wrap}.card{border-bottom:1px solid #ddd;padding:16px 0}.card img{display:block;max-width:100%;max-height:480px;margin-top:8px}.muted{color:#666}#status{white-space:pre-wrap;color:#8a2100}
</style>
<h1>Демо фото и ленты</h1>
<p class="muted">Войдите под двумя аккаунтами: первый загрузит публикацию, второй подпишется и увидит её в своей ленте.</p>
<p id="status" role="status">Нажмите «Войти» у автора и читателя.</p>
<div class="row">
  <section>
    <h2>Автор</h2>
    <label>Email <input id="authorEmail" type="email" autocomplete="off" value="demo.author@ai-system-design.local"></label>
    <label>Пароль <input id="authorPassword" type="password" autocomplete="new-password" value="DemoPass2026!"></label>
    <button type="button" onclick="login('author')">Войти</button>
    <div id="authorInfo" class="muted">Не вошли</div>
  </section>
  <section>
    <h2>Читатель</h2>
    <label>Email <input id="viewerEmail" type="email" autocomplete="off" value="demo.viewer@ai-system-design.local"></label>
    <label>Пароль <input id="viewerPassword" type="password" autocomplete="new-password" value="DemoPass2026!"></label>
    <button type="button" onclick="login('viewer')">Войти</button>
    <div id="viewerInfo" class="muted">Не вошли</div>
  </section>
</div>
<section>
  <h2>Опубликовать фото</h2>
  <label>Текст <input id="postText" type="text" maxlength="1000"></label>
  <label>Фото <input id="photo" type="file" accept="image/*"></label>
  <button onclick="uploadPost()">Загрузить и опубликовать</button>
  <button onclick="followAuthor()">Подписать читателя на автора</button>
</section>
<section>
  <h2>Лента читателя</h2>
  <button onclick="loadFeed()">Обновить ленту</button>
  <div id="feed"></div>
</section>
<script>
const accounts={author:null,viewer:null};
const el=id=>document.getElementById(id);
function say(message){el('status').textContent=message;}
async function request(path,options={}){
  const response=await fetch(path,options);
  const data=response.status===204?null:await response.json().catch(()=>null);
  if(!response.ok) throw new Error(data?.detail||`HTTP ${response.status}`);
  return data;
}
async function login(role){
  const prefix=role==='author'?'author':'viewer';
  const email=el(prefix+'Email').value.trim(),password=el(prefix+'Password').value;
  if(!email||!password) return say('Укажите email и пароль.');
  try{
    try{await request('/api/v1/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});}catch(_){}
    const auth=await request('/api/v1/auth/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});
    const me=await request('/api/v1/auth/me',{headers:{Authorization:`Bearer ${auth.access_token}`}});
    accounts[role]={token:auth.access_token,id:me.user_id};
    el(role+'Info').textContent=`Пользователь #${me.user_id}`;
    say(`${role==='author'?'Автор':'Читатель'} вошёл.`);
  }catch(error){el(role+'Info').textContent=`Ошибка входа: ${error.message}`;say(error.message);}
}
async function followAuthor(){
  if(!accounts.author||!accounts.viewer) return say('Сначала войдите под обоими аккаунтами.');
  if(accounts.author.id===accounts.viewer.id) return say('Для демо нужны два разных пользователя.');
  try{
    await request(`/api/v1/users/${accounts.author.id}/followers`,{method:'POST',headers:{Authorization:`Bearer ${accounts.viewer.token}`}});
    say(`Читатель подписан на пользователя #${accounts.author.id}.`);
  }catch(error){say(error.message==='follow already exists'?'Читатель уже подписан.':error.message);}
}
async function uploadPost(){
  if(!accounts.author) return say('Сначала войдите как автор.');
  const file=el('photo').files[0];
  if(!file) return say('Выберите фото.');
  const form=new FormData();form.append('file',file);form.append('text',el('postText').value);
  try{
    const post=await request('/api/v1/posts',{method:'POST',headers:{Authorization:`Bearer ${accounts.author.token}`},body:form});
    say(`Пост #${post.id} сохранён; лента обновится после обработки outbox.`);
    await loadFeed();
  }catch(error){say(error.message);}
}
async function loadFeed(){
  if(!accounts.viewer) return say('Сначала войдите как читатель.');
  try{
    const result=await request(`/api/v1/users/${accounts.viewer.id}/feed?limit=20`,{headers:{Authorization:`Bearer ${accounts.viewer.token}`}});
    const feed=el('feed');feed.replaceChildren();
    for(const post of result.items){
      const card=document.createElement('article');card.className='card';
      const caption=document.createElement('p');caption.textContent=post.text||'(без текста)';card.append(caption);
      const meta=document.createElement('small');meta.className='muted';meta.textContent=`Автор #${post.user_id} · пост #${post.id}`;card.append(meta);
      const image=document.createElement('img');image.src=post.image_url;image.alt=post.text||'Фото публикации';card.append(image);
      feed.append(card);
    }
    if(!result.items.length) feed.textContent='Лента пуста. Подпишитесь на автора и обновите ленту.';
    say(`Показано публикаций: ${result.items.length}`);
  }catch(error){say(error.message);}
}
</script>
</html>"""


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_page() -> HTMLResponse:
    return HTMLResponse(PAGE)
