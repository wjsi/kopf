import pytest

import kopf
from kopf._cogs.aiokits.aiovalues import Container
from kopf._cogs.clients.errors import APIConflictError, APIError, \
                                      APIForbiddenError, APIUnauthorizedError
from kopf._cogs.structs.references import MUTATING_WEBHOOK, VALIDATING_WEBHOOK
from kopf._core.engines.admission import configuration_manager
from kopf._core.intents.causes import WebhookType


@pytest.mark.parametrize('reason', set(WebhookType))
@pytest.mark.parametrize('selector', {VALIDATING_WEBHOOK, MUTATING_WEBHOOK})
async def test_nothing_happens_if_not_managed(
        mocker, settings, registry, insights, selector, reason, k8s_mocked):

    container = Container()
    mocker.patch.object(insights.ready_resources, 'wait')  # before the general Event.wait!
    mocker.patch.object(insights.backbone, 'wait_for')
    mocker.patch.object(container, 'as_changed')
    mocker.patch('asyncio.Event.wait')

    settings.admission.managed = None
    await configuration_manager(
        reason=reason,
        selector=selector,
        registry=registry,
        settings=settings,
        insights=insights,
        container=container,
    )

    assert not insights.ready_resources.wait.called
    assert not insights.backbone.wait_for.called
    assert not k8s_mocked.post.called
    assert not k8s_mocked.patch.called
    assert not container.as_changed.called


@pytest.mark.parametrize('reason', set(WebhookType))
@pytest.mark.parametrize('selector', {VALIDATING_WEBHOOK, MUTATING_WEBHOOK})
async def test_creation_is_attempted(
        mocker, settings, registry, insights, selector, resource, reason, k8s_mocked):

    container = Container()
    mocker.patch.object(container, 'as_changed', return_value=aiter([]))

    settings.admission.managed = 'xyz'
    await configuration_manager(
        reason=reason,
        selector=selector,
        registry=registry,
        settings=settings,
        insights=insights,
        container=container,
    )

    assert k8s_mocked.post.call_count == 1
    assert k8s_mocked.post.call_args_list[0][1]['url'].startswith('/apis/admissionregistration.k8s.io/')
    assert k8s_mocked.post.call_args_list[0][1]['payload']['metadata']['name'] == 'xyz'


@pytest.mark.parametrize('reason', set(WebhookType))
@pytest.mark.parametrize('selector', {VALIDATING_WEBHOOK, MUTATING_WEBHOOK})
async def test_creation_ignores_if_exists_already(
        mocker, settings, registry, insights, selector, resource, reason, k8s_mocked):

    container = Container()
    mocker.patch.object(container, 'as_changed', return_value=aiter([]))
    k8s_mocked.post.side_effect = APIConflictError({}, status=409)

    settings.admission.managed = 'xyz'
    await configuration_manager(
        reason=reason,
        selector=selector,
        registry=registry,
        settings=settings,
        insights=insights,
        container=container,
    )

    assert k8s_mocked.post.call_count == 1
    assert k8s_mocked.post.call_args_list[0][1]['url'].startswith('/apis/admissionregistration.k8s.io/')
    assert k8s_mocked.post.call_args_list[0][1]['payload']['metadata']['name'] == 'xyz'


@pytest.mark.parametrize('error', {APIError, APIForbiddenError, APIUnauthorizedError})
@pytest.mark.parametrize('reason', set(WebhookType))
@pytest.mark.parametrize('selector', {VALIDATING_WEBHOOK, MUTATING_WEBHOOK})
async def test_creation_escalates_on_errors(
        mocker, settings, registry, insights, selector, resource, reason, k8s_mocked, error):

    container = Container()
    mocker.patch.object(container, 'as_changed', return_value=aiter([]))
    k8s_mocked.post.side_effect = error({}, status=400)

    with pytest.raises(error):
        settings.admission.managed = 'xyz'
        await configuration_manager(
            reason=reason,
            selector=selector,
            registry=registry,
            settings=settings,
            insights=insights,
            container=container,
        )

    assert k8s_mocked.post.call_count == 1
    assert k8s_mocked.post.call_args_list[0][1]['url'].startswith('/apis/admissionregistration.k8s.io/')
    assert k8s_mocked.post.call_args_list[0][1]['payload']['metadata']['name'] == 'xyz'


@pytest.mark.parametrize('reason', set(WebhookType))
@pytest.mark.parametrize('selector', {VALIDATING_WEBHOOK, MUTATING_WEBHOOK})
async def test_patching_on_changes(
        mocker, settings, registry, insights, selector, resource, reason, k8s_mocked):

    @kopf.on.validate(*resource, registry=registry)
    def fn_v(**_): pass

    @kopf.on.mutate(*resource, registry=registry)
    def fn_m(**_): pass

    container = Container()
    mocker.patch.object(container, 'as_changed', return_value=aiter([
        {'url': 'https://hostname1/'},
        {'url': 'https://hostname2/'},
    ]))

    settings.admission.managed = 'xyz'
    await configuration_manager(
        reason=reason,
        selector=selector,
        registry=registry,
        settings=settings,
        insights=insights,
        container=container,
    )

    assert k8s_mocked.patch.call_count == 3
    assert k8s_mocked.patch.call_args_list[0][1]['url'].startswith('/apis/admissionregistration.k8s.io/')
    assert k8s_mocked.patch.call_args_list[0][1]['url'].endswith('/xyz')
    assert k8s_mocked.patch.call_args_list[1][1]['url'].startswith('/apis/admissionregistration.k8s.io/')
    assert k8s_mocked.patch.call_args_list[1][1]['url'].endswith('/xyz')
    assert k8s_mocked.patch.call_args_list[2][1]['url'].startswith('/apis/admissionregistration.k8s.io/')
    assert k8s_mocked.patch.call_args_list[2][1]['url'].endswith('/xyz')

    patch = k8s_mocked.patch.call_args_list[0][1]['payload']
    assert patch['webhooks']
    assert patch['webhooks'][0]['clientConfig']['url'].startswith('https://hostname1/')
    assert patch['webhooks'][0]['rules']
    assert patch['webhooks'][0]['rules'][0]['resources'] == ['kopfexamples']

    patch = k8s_mocked.patch.call_args_list[1][1]['payload']
    assert patch['webhooks']
    assert patch['webhooks'][0]['clientConfig']['url'].startswith('https://hostname2/')
    assert patch['webhooks'][0]['rules']
    assert patch['webhooks'][0]['rules'][0]['resources'] == ['kopfexamples']


@pytest.mark.parametrize('reason', set(WebhookType))
@pytest.mark.parametrize('selector', {VALIDATING_WEBHOOK, MUTATING_WEBHOOK})
async def test_patching_purges_non_permanent_webhooks(
        mocker, settings, registry, insights, selector, resource, reason, k8s_mocked):

    @kopf.on.validate(*resource, registry=registry, persistent=False)
    def fn_v(**_): pass

    @kopf.on.mutate(*resource, registry=registry, persistent=False)
    def fn_m(**_): pass

    container = Container()
    mocker.patch.object(container, 'as_changed', return_value=aiter([
        {'url': 'https://hostname/'},
    ]))

    settings.admission.managed = 'xyz'
    await configuration_manager(
        reason=reason,
        selector=selector,
        registry=registry,
        settings=settings,
        insights=insights,
        container=container,
    )

    assert k8s_mocked.patch.call_count == 2
    patch = k8s_mocked.patch.call_args_list[-1][1]['payload']
    assert not patch['webhooks']


@pytest.mark.parametrize('reason', set(WebhookType))
@pytest.mark.parametrize('selector', {VALIDATING_WEBHOOK, MUTATING_WEBHOOK})
async def test_patching_leaves_permanent_webhooks(
        mocker, settings, registry, insights, selector, resource, reason, k8s_mocked):

    @kopf.on.validate(*resource, registry=registry, persistent=True)
    def fn_v(**_): pass

    @kopf.on.mutate(*resource, registry=registry, persistent=True)
    def fn_m(**_): pass

    container = Container()
    mocker.patch.object(container, 'as_changed', return_value=aiter([
        {'url': 'https://hostname/'},
    ]))

    settings.admission.managed = 'xyz'
    await configuration_manager(
        reason=reason,
        selector=selector,
        registry=registry,
        settings=settings,
        insights=insights,
        container=container,
    )

    assert k8s_mocked.patch.call_count == 2
    patch = k8s_mocked.patch.call_args_list[-1][1]['payload']
    assert patch['webhooks'][0]['clientConfig']['url'].startswith('https://hostname/')
    assert patch['webhooks'][0]['rules']
    assert patch['webhooks'][0]['rules'][0]['resources'] == ['kopfexamples']


async def aiter(src):
    for item in src:
        yield item
