# -*- coding: utf-8 -*-
"""Direct Bilibili stream downloader; Android DASH muxing uses MediaMerger.java."""
import os
import re
import time
from urllib.parse import urlparse

import requests
from bili_api import new_session, get_play_info, BiliAPIError

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


class DownloadError(RuntimeError):
    pass


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_',
                  name or 'bilibili_video').strip(' .')
    return (name or 'bilibili_video')[:120]


def _headers(extra=None):
    h = {
        'Referer': 'https://www.bilibili.com/',
        'Origin': 'https://www.bilibili.com',
        'User-Agent': UA,
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    if extra:
        h.update(extra)
    return h


def _download_url(url, dest, session, progress=None, cancel=None):
    tmp = dest + '.part'
    offset = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    extra = {'Range': 'bytes=%d-' % offset} if offset else None

    with session.get(url, headers=_headers(extra), stream=True,
                     timeout=(20, 60)) as r:
        if offset and r.status_code != 206:
            offset = 0
            try:
                os.remove(tmp)
            except OSError:
                pass
        r.raise_for_status()
        total = int(r.headers.get('Content-Length') or 0) + offset
        mode = 'ab' if offset else 'wb'
        downloaded = offset
        with open(tmp, mode) as f:
            for chunk in r.iter_content(1024 * 256):
                if cancel and cancel.is_set():
                    raise DownloadError('下载已取消')
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
    os.replace(tmp, dest)
    return dest


def _try_urls(urls, dest, session, progress, cancel, log):
    """依次尝试主地址和备用地址，全部失败才报错，并列出每个地址的结果。"""
    errors = []
    for url in filter(None, urls):
        try:
            return _download_url(url, dest, session, progress, cancel)
        except Exception as exc:
            if cancel and cancel.is_set():
                raise DownloadError('下载已取消')
            host = urlparse(url).netloc
            resp = getattr(exc, 'response', None)
            code = getattr(resp, 'status_code', None)
            msg = '%s %s' % (host, ('HTTP %s' % code) if code else exc)
            errors.append(msg)
            if log:
                log('失败：' + msg)
    raise DownloadError('视频流下载失败：' + '；'.join(errors))


def _fetch_data(session, info, qn):
    play = get_play_info(session, info)
    data = play['data']
    if qn and qn not in (data.get('accept_quality') or []):
        raise DownloadError(
            '所选画质当前账号/视频不可用，请重新解析并选择实际可用画质。')
    if qn:
        from bili_api import _signed_playurl, _api_json, API
        try:
            data = _signed_playurl(session, info, qn)
        except Exception:
            params = {'cid': info['cid'], 'qn': qn,
                      'fnval': 1, 'fnver': 0, 'fourk': 1}
            if info.get('bvid'):
                params['bvid'] = info['bvid']
            else:
                params['avid'] = info.get('aid')
            data = _api_json(session, API + '/x/player/playurl', params)
    return data


def _download_once(info, cookie, quality, output_dir,
                   progress, cancel, log):
    os.makedirs(output_dir, exist_ok=True)
    session = new_session(cookie)
    qn = int(quality) if str(quality).isdigit() else 0
    data = _fetch_data(session, info, qn)

    title = safe_name(info.get('title'))
    final_path = os.path.join(output_dir, title + '.mp4')
    if os.path.exists(final_path):
        base, ext = os.path.splitext(final_path)
        final_path = base + '_' + str(int(time.time())) + ext

    durl = data.get('durl') or []
    dash = data.get('dash') or {}

    # 一体流（音视频已合并）
    if durl:
        item = durl[0]
        urls = [item.get('url')] + (item.get('backup_url') or [])
        if log:
            log('下载合并音视频流…')
        return _try_urls(urls, final_path, session, progress, cancel, log)

    # DASH（音视频分离，需要在 Android 上合并）
    videos = dash.get('video') or []
    audios = dash.get('audio') or []
    if not videos or not audios:
        raise DownloadError('B站没有返回可下载的音视频流。')

    video = None
    if qn:
        video = next((v for v in videos if int(v.get('id', 0)) == qn), None)
    if video is None:
        video = videos[0]
    audio = audios[0]

    def stream_urls(s):
        first = s.get('baseUrl') or s.get('base_url')
        backup = s.get('backupUrl') or s.get('backup_url') or []
        return [first] + list(backup)

    vpath = os.path.join(output_dir, title + '.video.m4s')
    apath = os.path.join(output_dir, title + '.audio.m4s')
    try:
        if log:
            log('下载视频轨道…')
        _try_urls(stream_urls(video), vpath, session,
                  progress, cancel, log)
        if log:
            log('下载音频轨道…')
        _try_urls(stream_urls(audio), apath, session,
                  progress, cancel, log)

        try:
            from jnius import autoclass
            merger = autoclass('org.bilibilidl.MediaMerger')
            ok = merger.merge(vpath, apath, final_path)
            if not ok:
                raise DownloadError('Android MediaMuxer 合并失败')
        except Exception as exc:
            raise DownloadError(
                '已下载音视频轨道，但合并 MP4 失败：%s。'
                '原始轨道保留在应用下载目录。' % exc)
        return final_path
    finally:
        # 只有合并成功后才删除原始轨道
        if os.path.isfile(final_path):
            for p in (vpath, apath):
                try:
                    os.remove(p)
                except OSError:
                    pass


def download_video(info, cookie, quality, output_dir,
                   progress=None, cancel=None, log=None):
    try:
        return _download_once(info, cookie, quality, output_dir,
                              progress, cancel, log)
    except DownloadError as exc:
        # 403 多半是下载地址与当前网络环境不匹配：重新取一次地址再试
        if '403' in str(exc) and not (cancel and cancel.is_set()):
            if log:
                log('收到 403，重新获取下载地址后重试…')
            return _download_once(info, cookie, quality, output_dir,
                                  progress, cancel, log)
        raise
