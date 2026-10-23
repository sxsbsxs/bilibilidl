# -*- coding: utf-8 -*-
"""Direct Bilibili stream downloader with strict selected-quality verification."""
import os
import re
import time
from urllib.parse import urlparse
import requests
from bili_api import new_session, get_play_info, BiliAPIError

UA = ('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36')
_MERGER = None
_MERGER_ERR = None

class DownloadError(RuntimeError):
    pass

def preload_merger():
    global _MERGER, _MERGER_ERR
    try:
        from jnius import autoclass
        _MERGER = autoclass('org.bilibilidl.MediaMerger')
        _MERGER_ERR = None
    except Exception as exc:
        _MERGER = None
        _MERGER_ERR = exc

def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', name or 'bilibili_video').strip(' .')
    return (name or 'bilibili_video')[:120]

def _headers(extra=None):
    h = {'Referer': 'https://www.bilibili.com/', 'Origin': 'https://www.bilibili.com',
         'User-Agent': UA, 'Accept': '*/*', 'Accept-Language': 'zh-CN,zh;q=0.9'}
    if extra: h.update(extra)
    return h

def _download_url(url, dest, session, progress=None, cancel=None):
    tmp = dest + '.part'
    offset = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    extra = {'Range': 'bytes=%d-' % offset} if offset else None
    with session.get(url, headers=_headers(extra), stream=True, timeout=(20, 60)) as r:
        if offset and r.status_code != 206:
            offset = 0
            try: os.remove(tmp)
            except OSError: pass
        r.raise_for_status()
        total = int(r.headers.get('Content-Length') or 0) + offset
        mode = 'ab' if offset else 'wb'
        downloaded = offset
        with open(tmp, mode) as f:
            for chunk in r.iter_content(1024 * 256):
                if cancel and cancel.is_set(): raise DownloadError('下载已取消')
                if not chunk: continue
                f.write(chunk); downloaded += len(chunk)
                if progress: progress(downloaded, total)
    os.replace(tmp, dest)
    return dest

def _try_urls(urls, dest, session, progress, cancel, log):
    errors = []
    for url in filter(None, urls):
        try: return _download_url(url, dest, session, progress, cancel)
        except Exception as exc:
            if cancel and cancel.is_set(): raise DownloadError('下载已取消')
            host = urlparse(url).netloc
            resp = getattr(exc, 'response', None)
            code = getattr(resp, 'status_code', None)
            msg = '%s %s' % (host, ('HTTP %s' % code) if code else exc)
            errors.append(msg)
            if log: log('失败：' + msg)
    raise DownloadError('视频流下载失败：' + '；'.join(errors))

def _params(info, qn, fnval=4048):
    p = {'cid': info['cid'], 'qn': int(qn), 'fnval': fnval, 'fnver': 0, 'fourk': 1,
         'platform': 'html5', 'high_quality': 1}
    if info.get('bvid'): p['bvid'] = info['bvid']
    else: p['avid'] = int(info.get('aid'))
    return p

def _playable_quality_ids(data):
    found = set()
    for stream in ((data.get('dash') or {}).get('video') or []):
        try:
            q = int(stream.get('id') or 0)
            if q: found.add(q)
        except (TypeError, ValueError): pass
    if data.get('durl'):
        try:
            q = int(data.get('quality') or 0)
            if q: found.add(q)
        except (TypeError, ValueError): pass
    return found

def _fetch_data(session, info, qn):
    """Never silently substitute 360P when a higher quality was selected."""
    try:
        play = get_play_info(session, info)
        data = play.get('data') or {}
    except Exception as exc:
        data = {}
        if not qn:
            raise DownloadError('无法获取可用播放信息：%s' % str(exc)[:160])
    if not qn:
        return data
    errors = []
    # Try signed WBI first, then the compatibility endpoint with DASH enabled.
    try:
        from bili_api import _signed_playurl
        result = _signed_playurl(session, info, qn)
        if qn in _playable_quality_ids(result):
            return result
        ids = sorted(_playable_quality_ids(result), reverse=True)
        errors.append('WBI接口实际返回%s' % (','.join(map(str, ids)) if ids else '无可用流'))
    except Exception as exc:
        errors.append('WBI接口失败：%s' % str(exc)[:100])
    try:
        from bili_api import _api_json, API
        result = _api_json(session, API + '/x/player/playurl', _params(info, qn, 4048))
        if qn in _playable_quality_ids(result):
            return result
        ids = sorted(_playable_quality_ids(result), reverse=True)
        errors.append('备用接口实际返回%s' % (','.join(map(str, ids)) if ids else '无可用流'))
    except Exception as exc:
        errors.append('备用接口失败：%s' % str(exc)[:100])
    labels = {127:'8K', 126:'杜比视界', 125:'HDR', 120:'4K', 116:'1080P60', 112:'1080P+',
              80:'1080P', 74:'720P60', 64:'720P', 32:'480P', 16:'360P', 15:'360P极速'}
    label = labels.get(qn, str(qn))
    raise DownloadError('所选%s并未返回对应视频流，已取消下载以避免错误地保存成360P。%s。请登录B站后重新解析，或选择接口实际返回的画质。' % (label, '；'.join(errors)))

def _merge(vpath, apath, final_path):
    merger = _MERGER
    if merger is None:
        from jnius import autoclass
        merger = autoclass('org.bilibilidl.MediaMerger')
    if not merger.merge(vpath, apath, final_path):
        raise DownloadError('Android MediaMuxer 合并失败')

def _download_once(info, cookie, quality, output_dir, progress, cancel, log):
    os.makedirs(output_dir, exist_ok=True)
    session = new_session(cookie)
    qn = int(quality) if str(quality).isdigit() else 0
    data = _fetch_data(session, info, qn)
    title = safe_name(info.get('title'))
    final_path = os.path.join(output_dir, title + '.mp4')
    if os.path.exists(final_path):
        base, ext = os.path.splitext(final_path); final_path = base + '_' + str(int(time.time())) + ext
    durl = data.get('durl') or []
    dash = data.get('dash') or {}
    if durl:
        actual = int(data.get('quality') or 0)
        if qn and actual != qn:
            raise DownloadError('接口返回的是%s而不是所选画质，已停止，避免生成错误画质文件。' % actual)
        item = durl[0]
        urls = [item.get('url')] + (item.get('backup_url') or [])
        if log: log('下载已核验画质的音视频流…')
        return _try_urls(urls, final_path, session, progress, cancel, log)
    videos = [v for v in (dash.get('video') or []) if isinstance(v, dict)]
    audios = [a for a in (dash.get('audio') or []) if isinstance(a, dict)]
    if not videos or not audios:
        raise DownloadError('B站没有返回可下载的音视频流。')
    if qn:
        video = next((v for v in videos if int(v.get('id') or 0) == qn), None)
        if video is None:
            actual = sorted({int(v.get('id') or 0) for v in videos}, reverse=True)
            raise DownloadError('所选画质%s没有对应视频轨道；接口实际返回：%s。已停止下载，不会再悄悄退回360P。' % (qn, ', '.join(map(str, actual))))
    else:
        video = max(videos, key=lambda v: int(v.get('id') or 0))
    audio = max(audios, key=lambda a: int(a.get('id') or 0))
    def stream_urls(s):
        first = s.get('baseUrl') or s.get('base_url')
        backup = s.get('backupUrl') or s.get('backup_url') or []
        return [first] + list(backup)
    vpath = os.path.join(output_dir, title + '.video.m4s')
    apath = os.path.join(output_dir, title + '.audio.m4s')
    try:
        if log: log('下载视频轨道（%s）…' % video.get('id'))
        _try_urls(stream_urls(video), vpath, session, progress, cancel, log)
        if log: log('下载音频轨道…')
        _try_urls(stream_urls(audio), apath, session, progress, cancel, log)
        if log: log('合并音视频…')
        try: _merge(vpath, apath, final_path)
        except Exception as exc:
            raise DownloadError('已下载音视频轨道，但合并 MP4 失败：%s。原始轨道保留在应用下载目录。' % exc)
        return final_path
    finally:
        if os.path.isfile(final_path):
            for p in (vpath, apath):
                try: os.remove(p)
                except OSError: pass

def download_video(info, cookie, quality, output_dir, progress=None, cancel=None, log=None):
    try:
        return _download_once(info, cookie, quality, output_dir, progress, cancel, log)
    except DownloadError as exc:
        if '403' in str(exc) and not (cancel and cancel.is_set()):
            if log: log('收到 403，重新获取下载地址后重试…')
            return _download_once(info, cookie, quality, output_dir, progress, cancel, log)
        raise
