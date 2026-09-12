"""Source-preserving chunking, keyword retrieval and optional real embeddings."""
import json
import re
import time

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ard.store import encode


def chunk_text(text, strategy='paragraph', chunk_size=600, overlap=60, delimiter='\n\n'):
    if not text.strip():
        raise ValueError('文档内容不能为空')
    if not 0 <= overlap < chunk_size or chunk_size < 1:
        raise ValueError('overlap必须小于chunk_size')
    patterns = {'paragraph': r'\n\s*\n', 'heading': r'(?m)(?=^#{1,6}\s)',
                'sentence': r'(?<=[。！？.!?])\s*', 'delimiter': re.escape(delimiter)}
    if strategy not in (*patterns, 'fixed'):
        raise ValueError('未知切片策略')
    cuts = [0, len(text)]
    if strategy != 'fixed':
        cuts += [m.end() for m in re.finditer(patterns[strategy], text)]
    cuts = sorted(set(cuts))
    chunks = []
    for begin, end in zip(cuts, cuts[1:]):
        pos = begin
        while pos < end:
            stop = min(pos + chunk_size, end)
            if text[pos:stop].strip():
                if len(chunks) >= 5000:
                    raise ValueError('单个文档最多5000个切片')
                chunks.append({'index': len(chunks), 'start': pos, 'end': stop, 'text': text[pos:stop]})
            if stop == end:
                break
            pos = stop - overlap
    return chunks


def add_document(service, pid, config, actor):
    text = config['text']
    chunks = chunk_text(text, config['strategy'], config['chunk_size'], config['overlap'], config['delimiter'])
    raw = encode({'text': text, 'chunks': chunks})
    with service.store.lock:
        service._quota(pid, len(raw))
        digest = service.store.blob(raw)
        return service.store.create('document', pid, {'name': config['name'], 'strategy': config['strategy'],
              'chunk_size': config['chunk_size'], 'overlap': config['overlap'], 'chunk_count': len(chunks),
              'sha256': digest, 'size_bytes': len(raw), 'creator': actor}, actor)


def search_documents(service, pid, query, top_k=5, threshold=0, mode='keyword'):
    start = time.perf_counter()
    if not query.strip():
        raise ValueError('检索词不能为空')
    candidates = []
    documents = service.store.list('document', pid)
    limit = 500 if mode in ('semantic', 'hybrid') else 10000
    if sum(doc['chunk_count'] for doc in documents) > limit:
        raise ValueError(f'本机{mode}检索最多{limit}个切片，请缩小项目范围')
    for doc in documents:
        data = json.loads(service.store.read_blob(doc['sha256']))
        for chunk in data['chunks']:
            candidates.append({**chunk, 'document_id': doc['id'], 'name': doc['name']})
    if not candidates:
        return {'matches': [], 'latency_ms': (time.perf_counter() - start) * 1000, 'mode': mode}
    texts = [x['text'] for x in candidates]
    lexical = np.zeros(len(candidates))
    if mode in ('keyword', 'hybrid'):
        vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(1, 3), max_features=80000,
                                     preprocessor=lambda t: re.sub(r'[^\w\u4e00-\u9fff]', '', t.lower()))
        try:
            matrix = vectorizer.fit_transform(texts)
            lexical = cosine_similarity(vectorizer.transform([query]), matrix)[0]
        except ValueError:
            pass  # Documents consisting only of punctuation have no searchable terms.
    scores = lexical
    if mode in ('semantic', 'hybrid'):
        if len(texts) > 500:
            raise ValueError('直接向量适配最多检索500个切片，请缩小项目范围')
        from ard.connectors import embeddings
        vectors = embeddings([query, *texts])
        semantic = np.clip(cosine_similarity(vectors[:1], vectors[1:])[0], 0, 1)
        scores = semantic if mode == 'semantic' else .5 * semantic + .5 * lexical
    matches = []
    for index in np.argsort(-scores, kind='stable')[:top_k]:
        if float(scores[index]) <= max(threshold, .000001):
            continue
        matches.append({**candidates[index], 'score': float(scores[index])})
    return {'matches': matches, 'latency_ms': (time.perf_counter() - start) * 1000, 'mode': mode,
            'searched_chunks': len(candidates), 'answer_type': 'retrieved_evidence'}
