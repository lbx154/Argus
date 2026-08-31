#[derive(Clone, Debug)]
pub struct BackendResilienceOptions {
    pub transient_failure_threshold: u32,
    pub health_retry_delays_ms: Vec<u64>,
    pub automatic_restart_delays_ms: Vec<u64>,
}

impl Default for BackendResilienceOptions {
    fn default() -> Self {
        Self {
            transient_failure_threshold: 3,
            health_retry_delays_ms: vec![1_000, 2_000],
            automatic_restart_delays_ms: vec![500, 1_500, 4_000],
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HealthDecision {
    Fail { failure_count: u32 },
    Recover { failure_count: u32 },
    Retry { failure_count: u32, delay_ms: u64 },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AutomaticRecoveryDecision {
    Allowed {
        attempt: u32,
        max_attempts: u32,
        delay_ms: u64,
    },
    Denied {
        attempts: u32,
        max_attempts: u32,
    },
}

#[derive(Clone, Debug)]
pub struct BackendResiliencePolicy {
    options: BackendResilienceOptions,
    consecutive_health_failures: u32,
    automatic_restart_attempts: u32,
}

impl BackendResiliencePolicy {
    pub fn new(options: BackendResilienceOptions) -> Self {
        assert!(options.transient_failure_threshold >= 1);
        assert!(!options.automatic_restart_delays_ms.is_empty());
        Self {
            options,
            consecutive_health_failures: 0,
            automatic_restart_attempts: 0,
        }
    }

    pub fn record_health_success(&mut self) {
        self.consecutive_health_failures = 0;
    }

    pub fn restart_attempt_count(&self) -> u32 {
        self.automatic_restart_attempts
    }

    pub fn max_automatic_restarts(&self) -> u32 {
        self.options.automatic_restart_delays_ms.len() as u32
    }

    pub fn record_health_failure(
        &mut self,
        identity_conflict: bool,
        process_alive: bool,
    ) -> HealthDecision {
        if identity_conflict {
            return HealthDecision::Fail {
                failure_count: self.consecutive_health_failures + 1,
            };
        }
        self.consecutive_health_failures += 1;
        let failure_count = self.consecutive_health_failures;
        if !process_alive || failure_count >= self.options.transient_failure_threshold {
            self.consecutive_health_failures = 0;
            return HealthDecision::Recover { failure_count };
        }
        let index = (failure_count.saturating_sub(1) as usize)
            .min(self.options.health_retry_delays_ms.len().saturating_sub(1));
        HealthDecision::Retry {
            failure_count,
            delay_ms: self
                .options
                .health_retry_delays_ms
                .get(index)
                .copied()
                .unwrap_or(1_000),
        }
    }

    pub fn begin_automatic_recovery(&mut self) -> AutomaticRecoveryDecision {
        let max_attempts = self.max_automatic_restarts();
        if self.automatic_restart_attempts >= max_attempts {
            return AutomaticRecoveryDecision::Denied {
                attempts: self.automatic_restart_attempts,
                max_attempts,
            };
        }
        self.automatic_restart_attempts += 1;
        let attempt = self.automatic_restart_attempts;
        AutomaticRecoveryDecision::Allowed {
            attempt,
            max_attempts,
            delay_ms: self.options.automatic_restart_delays_ms[(attempt - 1) as usize],
        }
    }

    pub fn mark_runtime_stable(&mut self) {
        self.consecutive_health_failures = 0;
        self.automatic_restart_attempts = 0;
    }

    pub fn reset(&mut self) {
        self.mark_runtime_stable();
    }
}

impl Default for BackendResiliencePolicy {
    fn default() -> Self {
        Self::new(BackendResilienceOptions::default())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transient_failures_are_bounded_before_recovery() {
        let mut policy = BackendResiliencePolicy::new(BackendResilienceOptions {
            transient_failure_threshold: 3,
            health_retry_delays_ms: vec![10, 20],
            automatic_restart_delays_ms: vec![30, 40, 50],
        });
        assert_eq!(
            policy.record_health_failure(false, true),
            HealthDecision::Retry {
                failure_count: 1,
                delay_ms: 10
            }
        );
        assert_eq!(
            policy.record_health_failure(false, true),
            HealthDecision::Retry {
                failure_count: 2,
                delay_ms: 20
            }
        );
        assert_eq!(
            policy.record_health_failure(false, true),
            HealthDecision::Recover { failure_count: 3 }
        );
        assert_eq!(
            policy.record_health_failure(true, true),
            HealthDecision::Fail { failure_count: 1 }
        );
    }

    #[test]
    fn automatic_restarts_are_limited_and_reset_after_stability() {
        let mut policy = BackendResiliencePolicy::default();
        for attempt in 1..=3 {
            assert!(
                matches!(policy.begin_automatic_recovery(), AutomaticRecoveryDecision::Allowed { attempt: current, .. } if current == attempt)
            );
        }
        assert!(matches!(
            policy.begin_automatic_recovery(),
            AutomaticRecoveryDecision::Denied { .. }
        ));
        policy.mark_runtime_stable();
        assert_eq!(policy.restart_attempt_count(), 0);
    }
}
