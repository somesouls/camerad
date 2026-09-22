(function(){
  var side=document.getElementById('side');
  var menu=document.getElementById('menuBtn');
  var pin=document.getElementById('pinBtn');
  if(menu){menu.setAttribute('aria-controls','side');menu.setAttribute('aria-expanded',side&&side.classList.contains('open')?'true':'false');}
  if(pin){
    pin.setAttribute('aria-pressed',document.body.classList.contains('sidebar-pinned')?'true':'false');
    pin.addEventListener('click',function(){pin.setAttribute('aria-pressed',document.body.classList.contains('sidebar-pinned')?'true':'false');});
  }
  function syncMenu(){if(menu)menu.setAttribute('aria-expanded',side&&side.classList.contains('open')?'true':'false');}
  if(menu)menu.addEventListener('click',function(){setTimeout(syncMenu,0);});
  var backdrop=document.getElementById('backdrop');
  if(backdrop)backdrop.addEventListener('click',function(){setTimeout(syncMenu,0);});
  document.addEventListener('keydown',function(event){
    if(event.key==='Escape'&&side&&side.classList.contains('open')){
      side.classList.remove('open');if(backdrop)backdrop.classList.remove('show');syncMenu();menu&&menu.focus();
    }
  });
  if(side)side.addEventListener('click',function(event){
    if(window.innerWidth<=820&&event.target.closest('a')){side.classList.remove('open');if(backdrop)backdrop.classList.remove('show');syncMenu();}
  });
})();
