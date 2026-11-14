import pytest
from app.services.media_tool_config import validate_plan_tool_limits
from app.services.managed_settings import validate_managed_setting
from app.services.application_settings import SettingValidationError


@pytest.mark.parametrize('value', [{'video':{'limit':1}}, {'music':{'limit':True}}, {'music':{'limit':-2}},
    {'portrait':{'limit':10001}}, {'portrait':{'limit':1,'period':'monthly'}}, {'portrait':{'limit':1,'extra':3}}])
def test_invalid_tool_quotas_are_rejected(value):
    with pytest.raises(ValueError):validate_plan_tool_limits(value)


def test_explicit_default_disabled_unlimited_and_weekly():
    assert validate_plan_tool_limits({'music':None, 'portrait':{'limit':-1,'period':'weekly'}})=={
        'music':None, 'portrait':{'limit':-1,'period':'weekly'}}
    assert validate_plan_tool_limits({'music':{'limit':0}})['music']['period']=='daily'


@pytest.mark.parametrize('key,value', [('portrait.price_irt','1.1'),('portrait.price_usdt','0.00001'),
    ('portrait.price_irt','NaN'),('portrait.price_usdt','Infinity'),('portrait.price_irt',True),('portrait.price_irt','-1')])
def test_invalid_portrait_price_cannot_be_saved(key,value):
    with pytest.raises(SettingValidationError):validate_managed_setting(key,value)


def test_zero_disables_sale_and_prices_preserve_currency_precision():
    assert validate_managed_setting('portrait.price_irt','0')=='0'
    assert validate_managed_setting('portrait.price_irt','25000')=='25000'
    assert validate_managed_setting('portrait.price_usdt','1.2345')=='1.2345'


def test_tool_copy_validates_placeholders_at_backend_boundary():
    assert validate_managed_setting('bot.tool_texts.fa',{'music_identity':'{artist} — {title}'})=={'music_identity':'{artist} — {title}'}
    for value in ('{title.__class__}','{unknown}','{title:>99999}'):
        with pytest.raises(SettingValidationError):validate_managed_setting('bot.tool_texts.fa',{'music_identity':value})
    with pytest.raises(SettingValidationError):validate_managed_setting('bot.tool_texts.fa',{'missing_text':'text'})
