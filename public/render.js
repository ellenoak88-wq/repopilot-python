import { Marked } from './vendor/marked.js';
import DOMPurify from './vendor/purify.js';
const escapeHTML = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const markdownParser = new Marked({ gfm: true, breaks: true, renderer: {
  html: ({ text }) => escapeHTML(text),
  image: ({ text }) => escapeHTML(text || ''),
} });

// Parse Markdown, sanitize it, then decorate only text outside code and links.
// Never strip quotes, parentheses or backslashes from model output.
export function renderAnswer(container, answer, sources = [], onCitation = () => {}, markdown = true) {
  container.replaceChildren();
  container.classList.toggle('markdown-body', markdown);
  const text = typeof answer === 'string' ? answer : '';
  if (markdown) {
    const fragment = DOMPurify.sanitize(markdownParser.parse(text), {
      RETURN_DOM_FRAGMENT: true,
      ALLOWED_TAGS: ['p','br','h1','h2','h3','h4','h5','h6','strong','em','del','blockquote','ul','ol','li','pre','code','hr','a','table','thead','tbody','tr','th','td'],
      ALLOWED_ATTR: ['href','title','start','align'],
      ALLOW_DATA_ATTR: false,
    });
    container.append(fragment);
    for (const link of container.querySelectorAll('a')) {
      const href = link.getAttribute('href') || '';
      if (!/^https?:\/\//i.test(href)) link.removeAttribute('href');
      else { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    }
  } else container.textContent = text;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    if (!walker.currentNode.parentElement.closest('code, pre, a')) nodes.push(walker.currentNode);
  }
  const known = new Set(sources.map(s => s.id));
  for (const node of nodes) {
    if (!/\[S\d+\]/.test(node.data)) continue;
    const fragment = document.createDocumentFragment();
    for (const part of node.data.split(/(\[S\d+\])/g)) {
      const id = part.slice(1, -1);
      if (/^\[S\d+\]$/.test(part) && known.has(id)) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'citation'; button.textContent = part;
        button.addEventListener('click', () => onCitation(id)); fragment.append(button);
      } else fragment.append(document.createTextNode(part));
    }
    node.replaceWith(fragment);
  }
}
