//! Keep a running backend untouched until the downloaded update is verified.

use std::future::Future;

pub(crate) async fn install_verified_update<D, S, I, E>(
    verified_download: D,
    stop_owned_backend: S,
    install: I,
) -> Result<(), E>
where
    D: Future<Output = Result<Vec<u8>, E>>,
    S: Future<Output = Result<(), E>>,
    I: FnOnce(Vec<u8>) -> Result<(), E>,
{
    // Tauri's download API verifies both package bytes and the trusted comment.
    // A failed/cancelled download must never interrupt a user's running task.
    let bytes = verified_download.await?;
    stop_owned_backend.await?;
    install(bytes)
}

#[cfg(test)]
mod tests {
    use super::install_verified_update;
    use std::{cell::RefCell, time::Duration};

    #[tokio::test]
    async fn verified_bytes_precede_owned_shutdown_and_installation() {
        let events = RefCell::new(Vec::new());
        let result = install_verified_update(
            async { events.borrow_mut().push("verified"); Ok::<_, &str>(vec![1, 2, 3]) },
            async { events.borrow_mut().push("stopped"); Ok(()) },
            |bytes| {
                assert_eq!(bytes, [1, 2, 3]);
                events.borrow_mut().push("installed");
                Ok(())
            },
        ).await;
        assert_eq!(result, Ok(()));
        assert_eq!(*events.borrow(), ["verified", "stopped", "installed"]);
    }

    #[tokio::test]
    async fn invalid_signature_never_stops_or_installs() {
        let result = install_verified_update(
            async { Err::<Vec<u8>, _>("invalid signature") },
            async { panic!("unverified download stopped the backend"); },
            |_| -> Result<(), &str> { panic!("unverified bytes reached the installer"); },
        ).await;
        assert_eq!(result, Err("invalid signature"));
    }

    #[tokio::test]
    async fn cancelled_download_never_polls_shutdown() {
        let operation = install_verified_update(
            std::future::pending::<Result<Vec<u8>, &str>>(),
            async { panic!("cancelled download stopped the backend"); },
            |_| -> Result<(), &str> { panic!("cancelled download installed"); },
        );
        assert!(tokio::time::timeout(Duration::from_millis(5), operation).await.is_err());
    }

    #[tokio::test]
    async fn a_failed_owned_stop_never_hands_bytes_to_the_installer() {
        let result = install_verified_update(
            async { Ok::<_, &str>(vec![1]) },
            async { Err("owned backend stop failed") },
            |_| -> Result<(), &str> { panic!("a running installation was handed to NSIS"); },
        ).await;
        assert_eq!(result, Err("owned backend stop failed"));
    }

    #[tokio::test]
    async fn failed_install_does_not_replay_or_automatically_resume_work() {
        let stops = RefCell::new(0);
        let result = install_verified_update(
            async { Ok::<_, &str>(vec![1]) },
            async { *stops.borrow_mut() += 1; Ok(()) },
            |_| Err("installer failed"),
        ).await;
        assert_eq!(result, Err("installer failed"));
        assert_eq!(*stops.borrow(), 1);
    }
}
