use crate::models::ProbeFailureKind;

/// Header success is not body success. A body timeout/truncated transport must
/// never be reclassified as a foreign backend just because JSON was not read.
pub async fn response_bytes(response: reqwest::Response) -> Result<Vec<u8>, ProbeFailureKind> {
    response.bytes().await.map(|bytes| bytes.to_vec()).map_err(|error| {
        if error.is_timeout() { ProbeFailureKind::Timeout } else { ProbeFailureKind::Network }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::TcpListener};
    use std::time::Duration;

    #[tokio::test]
    async fn a_stalled_body_is_a_timeout_not_an_identity_conflict() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut buffer = [0; 4096];
            let _ = socket.read(&mut buffer).await;
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n{").await.unwrap();
            tokio::time::sleep(Duration::from_millis(400)).await;
        });
        let response = reqwest::Client::builder().no_proxy().build().unwrap()
            .get(format!("http://{address}/api/meta")).timeout(Duration::from_millis(200))
            .send().await.unwrap();
        assert_eq!(response_bytes(response).await.unwrap_err(), ProbeFailureKind::Timeout);
        server.await.unwrap();
    }

    #[tokio::test]
    async fn a_truncated_body_is_a_transport_failure() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut buffer = [0; 4096];
            let _ = socket.read(&mut buffer).await;
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n{").await.unwrap();
        });
        let response = reqwest::Client::builder().no_proxy().build().unwrap()
            .get(format!("http://{address}/api/meta")).send().await.unwrap();
        assert_eq!(response_bytes(response).await.unwrap_err(), ProbeFailureKind::Network);
        server.await.unwrap();
    }
}
