# Removal inventory — service-core

Source-only inspection of the current tree, 30 September 2026. No compilation,
test execution or repository edits. This is a scoped inventory of the construction
API and legacy outbox paths, not a claim that every repository class was audited.
Public SDK APIs can have external callers that repository searches cannot reveal.

## 1. Remove now: redundant test methods

The exact signature/set assertions in PublicProgrammingModelContractTest already
cover these weaker public-surface checks. Keep the exact contract test unchanged.

[ServiceBuilderCompletenessTest.java](/Users/stefanorocco/apps/service-core/application/src/test/java/sdk/composition/ServiceBuilderCompletenessTest.java):

- `serviceBuilderIsFactoryOnly()`
- `authoritativeSurfaceBindsDeserializerToTopic()`
- `projectorSurfaceBindsInputAndSnapshotContractsToTopics()`

[ServiceDeploymentCapabilityMatrixTest.java](/Users/stefanorocco/apps/service-core/application/src/test/java/sdk/composition/ServiceDeploymentCapabilityMatrixTest.java):

- `authoritativeBuilderExposesOnlyAuthoritativeCapabilities()`
- `projectorBuilderExposesOnlyProjectorCapabilities()`

Keep its two behavioral tests: `authoritativeMayBindMultipleKafkaTopicsToDifferentDeserializers()`
and `projectorRejectsASecondKafkaSourceTopic()`. API reflection cannot replace them.
After removing the surface tests, remove their now-unused `publicMethodNames()` helper
and reflection/set imports.

Do not delete ServiceBuilderCompletenessTest wholesale on the duplication argument:
its remaining `serviceDefinitionExposesSdkServiceBackedCommandComposition()` checks
internal methods, not the public contract. It is a low-value shape-only test that can
be retired separately if the existing handler-factory behavior tests remain authoritative.

## 2. Remove together: unused internal convenience method and its test

[ServiceDefinition.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/composition/ServiceDefinition.java) — `configure(Consumer<ServiceDefinition>)`.

The only Java call site found is [ServiceBuilderStructureTest.java](/Users/stefanorocco/apps/service-core/application/src/test/java/sdk/composition/ServiceBuilderStructureTest.java) — `serviceDefinitionIsFluent()`. Delete that test with the method and its unused `Consumer` import. There is no production caller to migrate.

The other method, `builderHasNoFrameworkContainerField()`, is a weak architectural
check: it examines the static factory's fields and only two simple class names.
It does not prove framework independence in the actual builders. Optional removal,
not a proven duplicate; replace it with a dependency-level rule if that policy matters.

## 3. Retire as one change: old PostgreSQL claim/ack publisher

The supported builder now uses ServiceCoreFactory.createCheckpointed(). The old
ServiceCoreFactory.create(...) path instantiates OutboxDispatcher; its only external
Java callers found in this repository are two tests listed below. This is a legacy
subsystem, not a set of independently deletable files. Retirement is a public SDK
contract change and requires migrating those test callers and related configuration.

### Classes to remove after that retirement

- [OutboxDispatcher.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/outbox/transport/OutboxDispatcher.java)
- [OutboxRetryPolicy.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/outbox/transport/OutboxRetryPolicy.java)
- [ClaimedOutboxEvent.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/outbox/transport/protocol/event/ClaimedOutboxEvent.java)

### Methods to remove with it

- [ServiceCoreFactory.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/outbox/service/ServiceCoreFactory.java) — the four legacy `create(...)` overloads. Keep `createCheckpointed(...)` and `createEventWriter(...)`.
- [OutboxWorker.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/outbox/service/OutboxWorker.java) — the constructor overload taking `OutboxDispatcher`; preserve constructors taking `OutboxDispatchCycle` and all worker behavior.
- [OutboxRepository.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/outbox/persistence/OutboxRepository.java) — `claim`, `markPublished`, `markPublishedBatch`, `release`, `scheduleRetry`, `scheduleInfrastructureRetry`, `quarantine`. Keep `add`, `fetchAfter`, source-position allocation and event decoding.
- [PostgresOutboxRepository.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/infra/outbox/persistence/postgres/PostgresOutboxRepository.java) — `claim`, `markPublished`, `markPublishedBatch`, `release`, `scheduleRetry`, `scheduleInfrastructureRetry`, `quarantine`. Keep `add`, `fetchAfter`, source-position allocation and event decoding.

After those repository methods are gone, also remove their private-only support:
`executeOwnedUpdate`, `requireClaimArguments`, `requireInstanceId`,
`executeOwnedTransaction`, `rollbackAndSuppressIfActive`, `closeConnection`,
`OwnedTransactionWork` and `TransactionState` from PostgresOutboxRepository.
Its active `add` path uses the caller's business transaction; `fetchAfter` is read-only.
Do not remove the transaction outcome machinery in active transaction managers or maintenance.

OutboxRetryPolicy still appears in ServiceCoreConfig and ServiceRuntimeConfigLoader.
Remove the associated retry-policy configuration and legacy lease-specific validation
in the same retirement, with constructor/config/documentation migrations. Do not
blindly delete common worker polling, shutdown, retention or batch settings.

### Tests whose subject disappears with the old publisher

These test classes can be removed after retirement; they are not duplicates of the
current tests while their production subject still exists:

- [OutboxDispatcherBatchPublicationTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/transport/OutboxDispatcherBatchPublicationTest.java)
- [OutboxPoisonHandlingTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/transport/OutboxPoisonHandlingTest.java)
- [OutboxInfrastructureOutageSafetyTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/transport/OutboxInfrastructureOutageSafetyTest.java)
- [OutboxDispatcherSemanticsTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/OutboxDispatcherSemanticsTest.java)
- [PostgresOutboxTransitionFencingTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/PostgresOutboxTransitionFencingTest.java)
- [PostgresOutboxPoisonHandlingTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/PostgresOutboxPoisonHandlingTest.java)
- [PostgresOutboxBatchAcknowledgementTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/PostgresOutboxBatchAcknowledgementTest.java)
- [PostgresOutboxTransactionOutcomeTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/infra/outbox/persistence/postgres/PostgresOutboxTransactionOutcomeTest.java)

[PostgresOutboxIndexTuningTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/PostgresOutboxIndexTuningTest.java) — `schemaUsesStateSpecificClaimIndexes()` becomes obsolete only when the legacy indexes are retired from the current schema. Preserve historical migrations; use a forward migration for deployed databases.

[OutboxDispatcherMetricsIsolationTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/transport/OutboxDispatcherMetricsIsolationTest.java) is tied to the retired dispatcher, but its fail-open observability guarantee still matters. Ensure active worker/publisher coverage remains before removing the file; quarantine-specific expectations disappear with quarantine publication semantics.

### Migrate these tests; do not delete them

- [TwoServiceEndToEndTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/TwoServiceEndToEndTest.java) — preserve real HTTP/transaction/outbox/Kafka/inbox flow; migrate the legacy dispatcher/factory setup where used.
- [ServiceCoreFactoryTransactionTracingTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/service/ServiceCoreFactoryTransactionTracingTest.java) — preserve transaction tracing; migrate the legacy dispatcher/factory setup where used.
- [OutboxWorkerResilienceTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/service/OutboxWorkerResilienceTest.java) — preserve worker recovery and lifecycle behavior; migrate the legacy dispatcher/factory setup where used.
- [OutboxWorkerFailureVisibilityTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/service/OutboxWorkerFailureVisibilityTest.java) — preserve observable failures; migrate the legacy dispatcher/factory setup where used.
- [OutboxBurstDispatchTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/outbox/service/OutboxBurstDispatchTest.java) — preserve worker progress across cycles; migrate the legacy dispatcher/factory setup where used.
- [OutboxAfterCommitSignalTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/OutboxAfterCommitSignalTest.java) — preserve after-commit wakeup semantics; migrate the legacy dispatcher/factory setup where used.
- [OutboxShutdownDrainTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/OutboxShutdownDrainTest.java) — preserve bounded shutdown drain; migrate the legacy dispatcher/factory setup where used.

## 4. Conditional maintenance retirement

[PostgresOutboxMaintenance.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/infra/outbox/persistence/postgres/PostgresOutboxMaintenance.java) still has callers through PostgresServiceMaintenance. It is not currently dead.
After removing database-managed publication/quarantine state, retire:

- `requeueQuarantined(UUID)` and `requeueQuarantined(int)` in this class and the corresponding ServiceMaintenance/UnsupportedServiceMaintenance/PostgresServiceMaintenance methods.
- `deletePublishedBefore(...)` only when retention no longer relies on PUBLISHED/published_at. Do not substitute age-only deletion of PENDING rows: the Kafka checkpoint path leaves published rows PENDING too.
- The whole class only after all three methods have been retired or replaced safely.

[PostgresMaintenanceRecoveryTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/integration/PostgresMaintenanceRecoveryTest.java) — remove `requeueClearsEveryStaleClaimAndRetryField()` and
`bulkRequeueIsBoundedAndResetsQuarantineState()` when those APIs disappear.
Keep the inbox/inbound-failure retention assertions in `retentionDeletesOnlySafeTerminalState()`;
remove only its obsolete outbox-status portion.

[PostgresOutboxMaintenanceOutcomeTest.java](/Users/stefanorocco/apps/service-core/core/src/test/java/sdk/infra/outbox/persistence/postgres/PostgresOutboxMaintenanceOutcomeTest.java) remains necessary while this maintenance owner exists, even though it was recently added.
Its tests become removable only with that owner; active JDBC transaction tests must stay.

## 5. Optional API retirement, not proven dead code

[ApplicationEventDispatcher.java](/Users/stefanorocco/apps/service-core/application/src/main/java/sdk/application/execution/ApplicationEventDispatcher.java), [BusinessEventHandler.java](/Users/stefanorocco/apps/service-core/application/src/main/java/sdk/application/execution/BusinessEventHandler.java) and [ApplicationEventDispatcherTest.java](/Users/stefanorocco/apps/service-core/application/src/test/java/sdk/application/execution/ApplicationEventDispatcherTest.java) form a disconnected low-level API/test island: no production callers were found outside the dispatcher itself.
They can be retired together if the SDK deliberately drops that low-level API.
Update TransactionScope Javadoc and the historical retention wording in invariants.md.
The existing invariants explicitly preserve this test, so this is a contract retirement,
not an immediate deletion based solely on usage counts.

[DataSourceConnectionProvider.java](/Users/stefanorocco/apps/service-core/core/src/main/java/sdk/infra/outbox/persistence/postgres/DataSourceConnectionProvider.java) has no Java consumers in the scanned repository, but is a useful public adapter
for caller-owned DataSources. Keep unless support for that public integration is
explicitly being removed. The same caution applies to public OIDC, OpenTelemetry
and configuration adapters whose consumers may live outside this repository.

## 6. Keep

- PublicProgrammingModelContractTest: the canonical exact API guard.
- SingleConstructionPathTest: retain visibility, registration timing, command/query/codec wiring and missing/duplicate application behavior. Some public-signature assertions overlap, but the whole test is not redundant.
- KafkaOutboxCheckpointCoordinatorTest and KafkaOutboxCheckpointCoordinatorBrokerTest: client-boundary and physical-broker guarantees are different layers.
- CheckpointedOutboxDispatcherTest, source-position/sharding, telemetry, operation correlation and trace persistence tests.
- OutboxWorker, OutboxDispatchCycle, OutboxDispatchResult, OutboxDispatchFailure, EventPublisher, KafkaEventPublisher and LazyKafkaEventPublisher: shared active infrastructure.
- Actor, lifecycle, HTTP, transaction, security, inbox and physical chaos coverage.
- TestServiceBuilder/TestAuthoritativeServiceBuilder/TestProjectorServiceBuilder: used core test helpers, not dead classes. Replacing them with direct internal construction is a separate test refactor.

Recommended sequence: remove the five duplicate surface tests and the unused configure
method/test first; then retire the entire claim/ack publication path while migrating
its meaningful behavior tests. Address status-based maintenance and schema through a
separate explicit retention design. No production API should be removed merely because
its repository consumer count is zero.
