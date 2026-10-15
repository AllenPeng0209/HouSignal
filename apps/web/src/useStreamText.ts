import {useLayoutEffect,useState} from 'react';
import {nextStreamText} from './stream-text.js';

export function useStreamText(target:string,key:string){
 const [display,setDisplay]=useState({key,text:''});
 useLayoutEffect(()=>{
  let text=display.key===key?display.text:'';
  // Replacement and revocation take effect before the next paint.
  if(!target.startsWith(text)||window.matchMedia('(prefers-reduced-motion: reduce)').matches)text=target;
  if(display.key!==key||display.text!==text)setDisplay({key,text});
  if(text===target)return;
  let frame=0,previous=performance.now();
  const advance=(time:number)=>{
   text=nextStreamText(text,target,time-previous);previous=time;
   setDisplay({key,text});
   if(text!==target)frame=requestAnimationFrame(advance);
  };
  frame=requestAnimationFrame(advance);
  return()=>cancelAnimationFrame(frame);
 },[key,target]);
 if(display.key!==key)return '';
 return target.startsWith(display.text)?display.text:target;
}
