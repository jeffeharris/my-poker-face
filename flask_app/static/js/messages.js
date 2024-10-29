document.addEventListener('DOMContentLoaded', (event) => {
    //const socket = io.connect('http://localhost:5000'); // Change the URL to match your server

    socket.on('connect', function() {
        console.log('Connected to websocket server');
    });

    socket.on('disconnect', function() {
        console.log('Disconnected from the server');
    });

    socket.on('new_messages', function(data) {
        console.log("New messages received")
        let messages = data['game_messages'];
        displayMessages(messages);
    });

    document.getElementById('send-button').onclick = function() {
        sendUserMessage(socket);
    };

    document.getElementById('message-input').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            sendUserMessage(socket);
        }
    });

    document.getElementById('messages-toggle-bar').addEventListener('click', function() {
        let messages = document.getElementById('messages');
        messages.classList.toggle('collapsed');
    });

    // Initial fetch of messages to display
    fetchMessages();
});

function fetchMessages() {
    fetch(`/messages/${gameId}`)
        .then(response => response.json())
        .then(data => displayMessages(data))
}

function displayMessages(messages) {
    if (Array.isArray(messages)) {
        let messagesDiv = document.getElementById('messages-display');
        messagesDiv.innerHTML = '';  // Clear the existing messages

        messages.forEach(msg => {
            displayMessage(msg)
        });
    } else {
        console.error('Expected messages to be an array but got:', messages);
        // Optionally handle non-array messages differently or throw an error
    }

    // Scroll to the bottom when there is a new message
    let messagesDisplay = document.getElementById('messages-display');
    messagesDisplay.scrollTop = messagesDisplay.scrollHeight;
}

function displayMessage(msg){
    let messageContainer = document.createElement('div');
    messageContainer.classList.add('message-container');
    if (msg.message_type === 'user') {
        messageContainer.classList.add('user-message');
    } else if (msg.message_type === 'ai') {
        messageContainer.classList.add('ai-message');
    } else if (msg.message_type === 'table') {
        messageContainer.classList.add('table-message');
    }
    let headerDiv = document.createElement('div');
    headerDiv.classList.add('message-header');
    let senderSpan = document.createElement('span');
    senderSpan.textContent = msg.sender;
    senderSpan.classList.add('message-sender');
    headerDiv.appendChild(senderSpan);
    let timestampSpan = document.createElement('span');
    timestampSpan.textContent = msg.timestamp;
    timestampSpan.classList.add('message-timestamp');
    headerDiv.appendChild(timestampSpan);
    messageContainer.appendChild(headerDiv);
    let contentP = document.createElement('p');
    contentP.textContent = msg.content;
    contentP.classList.add('message-content');
    messageContainer.appendChild(contentP);
    let messagesDiv = document.getElementById('messages-display')
    messagesDiv.appendChild(messageContainer);
}

function sendUserMessage(socket) {
    let messageInput = document.getElementById('message-input');
    let message = messageInput.value;
    socket.emit('send_message', { message: message, game_id: gameId});
    messageInput.value = '';
    console.log(message)
}