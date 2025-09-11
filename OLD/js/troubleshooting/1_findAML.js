(()=>{const lists='[data-tid="threadList"],[data-tid="channelMessageList"],[data-tid="mainMessageList"]';
let bestI=-1,bestC=0;
document.querySelectorAll(lists).forEach((r,i)=>{
  const c=r.querySelectorAll('[role="group"],[data-tid="message"],[data-tid="messageCard"]').length;
  if(c>bestC){bestC=c;bestI=i;}
});
console.log('ROOT',bestI,bestC);
})();