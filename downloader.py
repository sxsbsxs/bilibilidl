# -*- coding: utf-8 -*-
"""Direct Bilibili stream downloader; Android DASH muxing uses MediaMerger.java."""
import os, re, json, time, threading
from pathlib import Path
import requests
from bili_api import new_session, get_play_info, BiliAPIError

class DownloadError(RuntimeError): pass

def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', name or 'bilibili_video').strip(' .')
    return (name or 'bilibili_video')[:120]

def _download_url(url, dest, session, progress=None, cancel=None):
    headers = {'Referer':'https://www.bilibili.com/'}
    tmp = dest + '.part'
    offset = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    if offset: headers['Range'] = 'bytes=%d-' % offset
    with session.get(url, headers=headers, stream=True, timeout=(20,60)) as r:
        if offset and r.status_code != 206:
            offset = 0
            try: os.remove(tmp)
            except OSError: pass
        r.raise_for_status()
        total = int(r.headers.get('Content-Length') or 0) + offset
        mode = 'ab' if offset else 'wb'
        downloaded = offset
        with open(tmp, mode) as f:
            for chunk in r.iter_content(1024*256):
                if cancel and cancel.is_set(): raise DownloadError('下载已取消')
                if not chunk: continue
                f.write(chunk); downloaded += len(chunk)
                if progress: progress(downloaded, total)
    os.replace(tmp, dest)
    return dest

def download_video(info, cookie, quality, output_dir, progress=None, cancel=None, log=None):
    os.makedirs(output_dir, exist_ok=True)
    session = new_session(cookie)
    play = get_play_info(session, info)
    data = play['data']
    qn = int(quality) if str(quality).isdigit() else 0
    if qn and qn not in (data.get('accept_quality') or []):
        raise DownloadError('所选画质当前账号/视频不可用，请重新解析并选择实际可用画质。')
    if qn:
        # Re-query exact quality. The endpoint's qn parameter selects requested quality.
        from bili_api import _signed_playurl, _api_json, API
        try:
            data = _signed_playurl(session, info, qn)
        except Exception:
            params = {'cid':info['cid'],'qn':qn,'fnval':1,'fnver':0,'fourk':1}
            if info.get('bvid'): params['bvid']=info['bvid']
            else: params['avid']=info.get('aid')
            data = _api_json(session, API+'/x/player/playurl', params)
    title = safe_name(info.get('title'))
    final_path = os.path.join(output_dir, title + '.mp4')
    if os.path.exists(final_path):
        base, ext = os.path.splitext(final_path); final_path = base+'_'+str(int(time.time()))+ext
    durl = data.get('durl') or []
    dash = data.get('dash') or {}
    if durl:
        item = durl[0]
        urls = [item.get('url')] + (item.get('backup_url') or [])
        last = None
        for url in filter(None, urls):
            try:
                if log: log('下载合并音视频流…')
                return _download_url(url, final_path, session, progress, cancel)
            except Exception as exc: last = exc
        raise DownloadError('视频流下载失败：%s' % last)
    videos = dash.get('video') or []
    audios = dash.get('audio') or []
    if not videos or not audios: raise DownloadError('B站没有返回可下载的音视频流。')
    video = next((v for v in videos if int(v.get('id',0)) == qn), None) if qn else None
    if video is None: video = videos[0]
    audio = audios[0]
    vpath = os.path.join(output_dir, title+'.video.m4s')
    apath = os.path.join(output_dir, title+'.audio.m4s')
    try:
        if log: log('下载视频轨道…')
        _download_url(video.get('baseUrl') or video.get('base_url'), vpath, session, progress, cancel)
        if log: log('下载音频轨道…')
        _download_url(audio.get('baseUrl') or audio.get('base_url'), apath, session, progress, cancel)
        # Android's MediaExtractor/MediaMuxer helper is supplied in src/.
        try:
            from jnius import autoclass
            merger = autoclass('org.bilibilidl.MediaMerger')
            ok = merger.merge(vpath, apath, final_path)
            if not ok: raise DownloadError('Android MediaMuxer 合并失败')
        except Exception as exc:
            raise DownloadError('已下载音视频轨道，但合并 MP4 失败：%s。原始轨道保留在应用下载目录。' % exc)
        return final_path
    finally:
        # Only remove tracks after a successful merge.
        if os.path.isfile(final_path):
            for p in (vpath, apath):
                try: os.remove(p)
                except OSError: pass
