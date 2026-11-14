import pytest
from app.services.media_tool_config import validate_plan_tool_limits
from app.services.managed_settings import validate_managed_setting
from app.services.application_settings import SettingValidationError


@pytest.mark.parametrize('value', [{'video':{'limit':1}}, {'music':{'limit':True}}, {'music':{'limit':-2}},
    {'music':{'limit':10001}}, {'music':{'limit':1,'period':'monthly'}}, {'music':{'limit':1,'extra':3}}])
def test_invalid_tool_quotas_are_rejected(value):
    with pytest.raises(ValueError):validate_plan_tool_limits(value)


def test_explicit_default_disabled_unlimited_and_weekly():
    assert validate_plan_tool_limits({'music':{'limit':-1,'period':'weekly'}})=={
        'music':{'limit':-1,'period':'weekly'}}
    assert validate_plan_tool_limits({'music':None}) == {'music':None}
    assert validate_plan_tool_limits({'music':{'limit':0}})['music']['period']=='daily'


def test_tool_copy_validates_placeholders_at_backend_boundary():
    assert validate_managed_setting('bot.tool_texts.fa',{'music_identity':'{artist} — {title}'})=={'music_identity':'{artist} — {title}'}
    for value in ('{title.__class__}','{unknown}','{title:>99999}'):
        with pytest.raises(SettingValidationError):validate_managed_setting('bot.tool_texts.fa',{'music_identity':value})
    with pytest.raises(SettingValidationError):validate_managed_setting('bot.tool_texts.fa',{'missing_text':'text'})
