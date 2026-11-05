const nav = document.querySelector('#topics');
const search = document.querySelector('#search');
const article = document.querySelector('#article');
let topics = [];
let active = '';

function showTopic(topic) {
  active = topic.id;
  history.replaceState(null, '', `#${topic.id}`);
  document.querySelectorAll('.topic').forEach(button => button.classList.toggle('active', button.dataset.id === active));
  document.querySelector('.crumb').textContent = `FluxFTP Help Center / ${topic.category} / ${topic.title}`;
  fetch(topic.file).then(response => {
    if (!response.ok) throw new Error('Article unavailable');
    return response.text();
  }).then(html => { article.innerHTML = html; window.scrollTo({top:0,behavior:'smooth'}); })
    .catch(() => { article.innerHTML = '<h1>Help unavailable</h1><p class="error">This article could not be loaded. Check your connection and try again.</p>'; });
}

function renderTopics() {
  const terms = search.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const filtered = topics.filter(topic => terms.every(term => topic._search.includes(term)));
  nav.replaceChildren();
  let category = '';
  for (const topic of filtered) {
    if (topic.category !== category) {
      category = topic.category;
      const heading = document.createElement('div');
      heading.className = 'group'; heading.textContent = category; nav.append(heading);
    }
    const button = document.createElement('button');
    button.className = 'topic'; button.dataset.id = topic.id; button.textContent = topic.title;
    button.onclick = () => showTopic(topic); nav.append(button);
  }
  if (!filtered.length) nav.textContent = 'No matching topics.';
  if (!filtered.some(topic => topic.id === active) && filtered.length) showTopic(filtered[0]);
}

search.addEventListener('input', renderTopics);
fetch('manifest.json').then(response => response.json()).then(async manifest => {
  topics = manifest.topics;
  await Promise.all(topics.map(async topic => {
    try { const response = await fetch(topic.file); topic._search = `${topic.title} ${topic.category} ${topic.summary} ${topic.keywords} ${await response.text()}`.replace(/<[^>]*>/g, ' ').toLocaleLowerCase(); }
    catch { topic._search = `${topic.title} ${topic.summary} ${topic.keywords}`.toLocaleLowerCase(); }
  }));
  renderTopics();
  const initial = topics.find(topic => topic.id === location.hash.slice(1));
  if (initial) showTopic(initial);
}).catch(() => { article.innerHTML = '<h1>Help unavailable</h1><p class="error">The Help Center index could not be loaded.</p>'; });
