"""Social media LLM operations: render prompt, call provider, validate output."""

from pydantic import ValidationError

from app.core.llm.base import LLMProvider, LLMTask
from app.core.llm.calls import LLMCall, parse_json_object, short_error, timed_complete
from app.core.llm.prompts import PROMPTS_DIR, PromptTemplate, load_prompt
from app.social.channels import CHANNEL_SPECS
from app.social.facts import FACT_LABELS, BriefingFacts, formatted_facts
from app.social.llm import fake_heuristics as _fake_heuristics  # noqa: F401 - offline demo
from app.social.llm.schemas import AssessmentOutput, DraftOutput
from app.social.models import CHANNEL_LABELS, Channel
from app.social.profiles import ADDRESS_FORM_LABELS, SocialConfig

SOCIAL_PROMPTS_DIR = PROMPTS_DIR / "social"
PROMPT_VERSIONS: dict[LLMTask, str] = {"post_draft": "v1", "post_assess": "v1"}


class SocialLLMClient:
    def __init__(
        self, provider: LLMProvider, prompts: dict[LLMTask, PromptTemplate] | None = None
    ) -> None:
        self.provider = provider
        self.prompts = prompts or {
            task: load_prompt(task, version, SOCIAL_PROMPTS_DIR)
            for task, version in PROMPT_VERSIONS.items()
        }

    def draft_post(
        self,
        config: SocialConfig,
        *,
        area_key: str,
        occasion_key: str,
        channel: Channel,
        facts: BriefingFacts,
        notes: str,
        campaign_step: str | None = None,
    ) -> LLMCall[DraftOutput]:
        brand = config.brand
        area = brand.area(area_key)
        template = self.prompts["post_draft"]
        prompt = template.render(
            brand_name=brand.name,
            brand_description=brand.description,
            channel=channel.value,
            channel_label=CHANNEL_LABELS[channel],
            max_length=CHANNEL_SPECS[channel].max_length,
            area_label=area.label,
            tone=area.tone,
            address_form=area.address_form.value,
            address_form_label=ADDRESS_FORM_LABELS[area.address_form],
            emojis_allowed=area.emojis.allowed,
            emojis_max=area.emojis.max,
            forbidden_terms=brand.forbidden_terms,
            max_hashtags=brand.max_hashtags,
            occasion_label=config.industry.occasion_type(occasion_key).label,
            campaign_step=campaign_step,
            facts=[(FACT_LABELS[key], value) for key, value in formatted_facts(facts).items()],
            notes=notes,
            call_to_action=brand.call_to_action_for(area_key),
            hashtags=brand.fixed_hashtags(area_key),
            examples=area.examples,
        )
        completion, error, duration = timed_complete(self.provider, "post_draft", prompt)
        call: LLMCall[DraftOutput] = LLMCall(
            "post_draft", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is not None:
            try:
                call.output = DraftOutput.model_validate(parse_json_object(completion.text))
            except (ValueError, ValidationError) as exc:
                call.error = f"invalid output: {short_error(exc)}"
        return call

    def assess_post(self, config: SocialConfig, text: str) -> LLMCall[AssessmentOutput]:
        """Assessment of health claims, named persons and address form. Only the post text
        is sent (data minimisation)."""
        template = self.prompts["post_assess"]
        prompt = template.render(industry=config.industry_key, text=text)
        completion, error, duration = timed_complete(self.provider, "post_assess", prompt)
        call: LLMCall[AssessmentOutput] = LLMCall(
            "post_assess", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is not None:
            try:
                call.output = AssessmentOutput.model_validate(parse_json_object(completion.text))
            except (ValueError, ValidationError) as exc:
                call.error = f"invalid output: {short_error(exc)}"
        return call
