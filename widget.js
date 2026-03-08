(function () {

const chatHTML = `
<div id="assistify-chat-button">
💬
</div>

<div id="assistify-chat-box">
<div id="assistify-header">
Assistify AI
<span id="assistify-close">✕</span>
</div>

<div id="assistify-messages"></div>

<div id="assistify-input-area">
<input id="assistify-input" placeholder="Typ je vraag..." />
<button id="assistify-send">Send</button>
</div>
</div>
`;

const style = document.createElement("style");
style.innerHTML = `
#assistify-chat-button{
position:fixed;
bottom:20px;
right:20px;
background:#111827;
color:white;
width:60px;
height:60px;
border-radius:50%;
display:flex;
align-items:center;
justify-content:center;
font-size:24px;
cursor:pointer;
z-index:9999;
}

#assistify-chat-box{
position:fixed;
bottom:90px;
right:20px;
width:320px;
height:420px;
background:white;
border-radius:12px;
box-shadow:0 10px 30px rgba(0,0,0,0.2);
display:none;
flex-direction:column;
overflow:hidden;
z-index:9999;
}

#assistify-header{
background:#111827;
color:white;
padding:12px;
font-weight:bold;
display:flex;
justify-content:space-between;
}

#assistify-messages{
flex:1;
padding:10px;
overflow-y:auto;
font-size:14px;
}

.assistify-user{
text-align:right;
margin:6px 0;
}

.assistify-ai{
text-align:left;
margin:6px 0;
}

#assistify-input-area{
display:flex;
border-top:1px solid #ddd;
}

#assistify-input{
flex:1;
padding:10px;
border:none;
outline:none;
}

#assistify-send{
background:#111827;
color:white;
border:none;
padding:10px 14px;
cursor:pointer;
}
`;

document.head.appendChild(style);
document.body.insertAdjacentHTML("beforeend", chatHTML);

const button = document.getElementById("assistify-chat-button");
const box = document.getElementById("assistify-chat-box");
const close = document.getElementById("assistify-close");

button.onclick = () => box.style.display = "flex";
close.onclick = () => box.style.display = "none";

const sendBtn = document.getElementById("assistify-send");
const input = document.getElementById("assistify-input");
const messages = document.getElementById("assistify-messages");

sendBtn.onclick = sendMessage;

function sendMessage(){

const text = input.value;
if(!text) return;

messages.innerHTML += `<div class="assistify-user">${text}</div>`;
input.value="";

fetch("https://assistifyai.nl/api/chat",{
method:"POST",
headers:{
"Content-Type":"application/json"
},
body:JSON.stringify({message:text})
})
.then(r=>r.json())
.then(data=>{
messages.innerHTML += `<div class="assistify-ai">${data.reply}</div>`;
messages.scrollTop = messages.scrollHeight;
});

}

})();
