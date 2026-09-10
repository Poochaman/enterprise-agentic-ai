import {refreshConfig,status} from './common.js';
const app=location.pathname.split('/')[1];
const titles={'agent-control-room':'Agent Control Room','programme-intelligence':'Programme Intelligence','model-evaluation':'Model Evaluation'};
try{if(!Object.hasOwn(titles,app))throw new Error('Unknown example');document.title=titles[app]+' · AI Systems Lab';document.querySelector(`[data-nav="${app}"]`).setAttribute('aria-current','page');await refreshConfig();const module=await import(`/${app}/app.js`);await module.start();}catch(error){status(error.message,'error');document.querySelector('#app').textContent='The example could not load. Check that the local server is running, then reload.';}
