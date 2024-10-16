document.getElementById('messages-toggle-bar').addEventListener('click', function() {
    let messages = document.getElementById('messages');
    messages.classList.toggle('collapsed');
});

// Fetch and display messages
function fetchMessages() {
    fetch('/messages')
        .then(response => response.json())
        .then(data => {
            let messagesDiv = document.getElementById('messages-display');
            // Uncomment the following line if you want to clear previous messages
            // messagesDiv.innerHTML = '';
            data.forEach(msg => {
                // Create a container for each message
                let messageContainer = document.createElement('div');
                messageContainer.classList.add('message-container');

                // Add classes based on message_type
                if (msg.message_type === 'user') {
                    messageContainer.classList.add('user-message');
                } else if (msg.message_type === 'ai') {
                    messageContainer.classList.add('ai-message');
                } else if (msg.message_type === 'table') {
                    messageContainer.classList.add('table-message');
                }

                // Create and append the header containing sender and timestamp
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

                // Create and append the content paragraph
                let contentP = document.createElement('p');
                contentP.textContent = msg.content;
                contentP.classList.add('message-content');
                messageContainer.appendChild(contentP);

                // Append the message container to the messages display div
                messagesDiv.appendChild(messageContainer);
            });
        });
}

// Send a new message
function sendMessage() {
    let messageInput = document.getElementById('message-input');
    let message = messageInput.value;

    fetch('/messages', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ message: message })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            messageInput.value = '';
            fetchMessages();
        } else {
            alert('Error sending message');
        }
    });
}

// Initial message fetch
// fetchMessages();     # TODO: enable this when we start working on the messaging feature again