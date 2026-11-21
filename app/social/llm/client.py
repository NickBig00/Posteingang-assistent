"""Social media LLM operations: render prompt, call provider, validate output."""

from pydantic import BaseModel, ValidationError

from app.core.llm.base import LLMProvider, LLMTask
from app.core.llm.calls import LLMCall, parse_json_object, short_error, timed_complete
from app.core.llm.prompts import PROMPTS_DIR, PromptTemplate, load_prompt
from app.social.channels import CHANNEL_SPECS
from app.social.facts import FACT_LABELS, BriefingFacts, FactKey, formatted_facts
from app.social.llm import fake_heuristics as _fake_heuristics  # noqa: F401 - offline demo
from app.social.llm.schemas import (
    AssessmentOutput,
    CarouselPlanOutput,
    DraftOutput,
    GraphicTextOutput,
    ImagePromptOutput,
)
from app.social.models import CHANNEL_LABELS, Channel
from app.social.profiles import ADDRESS_FORM_LABELS, SocialConfig

SOCIAL_PROMPTS_DIR = PROMPTS_DIR / "social"
PROMPT_VERSIONS: dict[LLMTask, str] = {
    "post_draft": "v2",
    "post_assess": "v2",
    "image_prompt": "v1",
    "post_revise": "v1",
    "image_prompt_revise": "v1",
    "graphic_text": "v1",
    "graphic_text_revise": "v1",
    "carousel_plan": "v1",
}


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
        missing_facts: list[FactKey] | None = None,
    ) -> LLMCall[DraftOutput]:
        """Draft for one channel. `missing_facts`: facts the previous attempt dropped or
        changed; the prompt names them explicitly (second attempt)."""
        brand = config.brand
        area = brand.area(area_key)
        formatted = formatted_facts(facts)
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
            facts=[(FACT_LABELS[key], value) for key, value in formatted.items()],
            missing_facts=[(FACT_LABELS[key], formatted[key]) for key in missing_facts or []],
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

    def image_prompt(
        self, config: SocialConfig, *, area_key: str, occasion_key: str, notes: str
    ) -> LLMCall[ImagePromptOutput]:
        """Description of an AI mood image from the briefing and the area's image style.

        Only occasion, area, key points and style are sent: no facts, no names (the key
        points must not contain any, and the code check rejects them in the result).
        """
        area = config.brand.area(area_key)
        style = area.image_style
        template = self.prompts["image_prompt"]
        prompt = template.render(
            occasion_label=config.industry.occasion_type(occasion_key).label,
            area_label=area.label,
            notes=notes,
            colors=style.colors if style else "passend zur Marke",
            mood=style.mood if style else area.tone,
            motifs=style.motifs if style else "passend zum Anlass",
            exclusions=style.exclusions if style else [],
        )
        completion, error, duration = timed_complete(self.provider, "image_prompt", prompt)
        call: LLMCall[ImagePromptOutput] = LLMCall(
            "image_prompt", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is not None:
            try:
                call.output = ImagePromptOutput.model_validate(parse_json_object(completion.text))
            except (ValueError, ValidationError) as exc:
                call.error = f"invalid output: {short_error(exc)}"
        return call

    def _structured[T: BaseModel](
        self, task: LLMTask, schema: type[T], **context: object
    ) -> LLMCall[T]:
        """Render the task's prompt, call the provider once and validate the JSON answer."""
        template = self.prompts[task]
        prompt = template.render(**context)
        completion, error, duration = timed_complete(self.provider, task, prompt)
        call: LLMCall[T] = LLMCall(
            task, template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is not None:
            try:
                call.output = schema.model_validate(parse_json_object(completion.text))
            except (ValueError, ValidationError) as exc:
                call.error = f"invalid output: {short_error(exc)}"
        return call

    def revise_post(
        self,
        config: SocialConfig,
        *,
        area_key: str,
        channel: Channel,
        facts: BriefingFacts,
        text: str,
        hashtags: list[str],
        instruction: str,
    ) -> LLMCall[DraftOutput]:
        """New version of a post following a person's instruction (SPEC_social 13.8).

        Sent: post text, hashtags, facts, tone and the instruction – not the key points.
        """
        brand = config.brand
        area = brand.area(area_key)
        return self._structured(
            "post_revise",
            DraftOutput,
            brand_name=brand.name,
            channel=channel.value,
            channel_label=CHANNEL_LABELS[channel],
            max_length=CHANNEL_SPECS[channel].max_length,
            area_label=area.label,
            tone=area.tone,
            address_form_label=ADDRESS_FORM_LABELS[area.address_form],
            forbidden_terms=brand.forbidden_terms,
            max_hashtags=brand.max_hashtags,
            facts=[(FACT_LABELS[k], v) for k, v in formatted_facts(facts).items()],
            text=text,
            hashtags=hashtags,
            instruction=instruction,
        )

    def revise_image_prompt(
        self, config: SocialConfig, *, area_key: str, prompt: str, instruction: str
    ) -> LLMCall[ImagePromptOutput]:
        """New version of an image prompt following a person's instruction."""
        style = config.brand.area(area_key).image_style
        return self._structured(
            "image_prompt_revise",
            ImagePromptOutput,
            prompt=prompt,
            instruction=instruction,
            colors=style.colors if style else "passend zur Marke",
            mood=style.mood if style else config.brand.area(area_key).tone,
            motifs=style.motifs if style else "passend zum Anlass",
            exclusions=style.exclusions if style else [],
        )

    def graphic_text(
        self,
        config: SocialConfig,
        *,
        area_key: str,
        occasion_key: str,
        notes: str,
        post_text: str,
    ) -> LLMCall[GraphicTextOutput]:
        """Texts for the info graphic from key points and post draft (no facts are sent)."""
        brand = config.brand
        area = brand.area(area_key)
        return self._structured(
            "graphic_text",
            GraphicTextOutput,
            brand_name=brand.name,
            area_label=area.label,
            tone=area.tone,
            address_form_label=ADDRESS_FORM_LABELS[area.address_form],
            forbidden_terms=brand.forbidden_terms,
            occasion_label=config.industry.occasion_type(occasion_key).label,
            notes=notes,
            post_text=post_text,
        )

    def revise_graphic_text(
        self,
        config: SocialConfig,
        *,
        area_key: str,
        texts: dict[str, object],
        instruction: str,
    ) -> LLMCall[GraphicTextOutput]:
        """New version of the info graphic texts following a person's instruction."""
        brand = config.brand
        area = brand.area(area_key)
        return self._structured(
            "graphic_text_revise",
            GraphicTextOutput,
            brand_name=brand.name,
            tone=area.tone,
            address_form_label=ADDRESS_FORM_LABELS[area.address_form],
            forbidden_terms=brand.forbidden_terms,
            instruction=instruction,
            **texts,
        )

    def carousel_plan(
        self,
        config: SocialConfig,
        *,
        area_key: str,
        occasion_key: str,
        notes: str,
        post_text: str,
    ) -> LLMCall[CarouselPlanOutput]:
        """Suggested split into carousel pages (no facts are sent; code adds them)."""
        brand = config.brand
        area = brand.area(area_key)
        return self._structured(
            "carousel_plan",
            CarouselPlanOutput,
            brand_name=brand.name,
            area_label=area.label,
            tone=area.tone,
            address_form_label=ADDRESS_FORM_LABELS[area.address_form],
            forbidden_terms=brand.forbidden_terms,
            occasion_label=config.industry.occasion_type(occasion_key).label,
            notes=notes,
            post_text=post_text,
        )
