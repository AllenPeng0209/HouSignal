import {useState} from 'react';
import {Icon,Modal} from './components';
import type {CatalogItem} from './ContentCatalog';

const platformLabels:Record<string,string>={bilibili:'B站',douyin:'抖音',xiaohongshu:'小红书'};
function httpsUrl(value:unknown):URL|null {
 if(typeof value!=='string')return null;
 try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&!url.port?url:null;}catch{return null;}
}
export function catalogSourceUrl(item:CatalogItem):string|null {
 const url=httpsUrl(item.url);if(!url)return null;
 const hosts:Record<string,string[]>={bilibili:['www.bilibili.com','bilibili.com'],douyin:['www.douyin.com','douyin.com'],xiaohongshu:['www.xiaohongshu.com','xiaohongshu.com']};
 if(!hosts[item.platform]?.includes(url.hostname)||!item.content_id||!/^[A-Za-z0-9]+$/.test(item.content_id))return null;
 const paths=item.platform==='xiaohongshu'?[`/explore/${item.content_id}`,`/discovery/item/${item.content_id}`]:[`/video/${item.content_id}`];
 return paths.some(path=>url.pathname===path||url.pathname===`${path}/`)?url.href:null;
}
export function catalogPlayerUrl(item:CatalogItem):string|null {
 if(item.platform!=='bilibili'||!/^BV[0-9A-Za-z]{10}$/.test(item.content_id)||!catalogSourceUrl(item))return null;
 // The supplied player_url is never used as an iframe source.
 return `https://player.bilibili.com/player.html?bvid=${item.content_id}&p=1&autoplay=1&muted=0&poster=1`;
}
export function catalogCoverUrl(item:CatalogItem):string|null {
 const url=httpsUrl(item.cover_url);if(!url)return null;
 return ['hdslb.com','biliimg.com'].some(host=>url.hostname===host||url.hostname.endsWith(`.${host}`))?url.href:null;
}
export function CatalogVideoCover({item,onOpen}:{item:CatalogItem;onOpen:()=>void}) {
 const [failedUrl,setFailedUrl]=useState(''),cover=catalogCoverUrl(item),player=catalogPlayerUrl(item),source=catalogSourceUrl(item),canOpen=Boolean(player||source);
 const label=platformLabels[item.platform]||item.platform;
 return <button type="button" className="catalog-video-cover" aria-label={`${player?'播放':'打开原作'}：${item.title}`} disabled={!canOpen} onClick={onOpen}>
  {cover&&cover!==failedUrl?<img src={cover} alt={`${item.title} 视频封面`} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={()=>setFailedUrl(cover)}/>:<span className="catalog-video-cover-placeholder"><Icon name="studio" size={34}/><span>{label}视频</span></span>}
  {canOpen&&<span className="catalog-video-play" aria-hidden="true"><Icon name={player?'play':'external'} size={23}/></span>}
  <span className="catalog-video-cover-label">{label}{canOpen&&` · ${player?'点击播放':'打开原作'}`}</span>
 </button>;
}
export function CatalogVideoPlayer({item,onClose}:{item:CatalogItem;onClose:()=>void}) {
 const player=catalogPlayerUrl(item),source=catalogSourceUrl(item),label=platformLabels[item.platform]||item.platform;
 return <Modal title={item.title} onClose={onClose} wide><div className="catalog-video-player">
  {player?<div className="catalog-video-frame"><iframe src={player} title={`B站播放器：${item.title}`} allow="autoplay; fullscreen; picture-in-picture" allowFullScreen referrerPolicy="strict-origin-when-cross-origin"/></div>:<div className="catalog-video-original"><Icon name="studio" size={36}/><p>前往{label}观看这条作品</p></div>}
  <div className="catalog-video-player-footer">{player&&<span>无法播放时，可前往B站观看。</span>}{source?<a href={source} target="_blank" rel="noreferrer">在{label}打开 <Icon name="external" size={14}/></a>:<span>原作链接暂不可用</span>}</div>
 </div></Modal>;
}
