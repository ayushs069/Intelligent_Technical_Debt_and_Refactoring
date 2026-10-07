"""Independent human review form, with no model rankings or future labels."""
import json
import streamlit as st

from agents.prompts import read_function_source, render_item
from evaluation.expert import save_rater


def render_review(items, sample, directory):
    st.header('Independent expert review')
    st.write('Score refactoring urgency from 1 (leave alone) to 10 (fix first). '
             'Use only the evidence below. Complete this before viewing algorithm rankings.')
    by_id = {i['id']: i for i in items}
    with st.form('blind_review'):
        rater = st.text_input('Reviewer name or identifier')
        scores = {}
        for n, item_id in enumerate(sample, 1):
            item = by_id.get(item_id)
            if item is None:
                continue
            with st.expander(f"{n}. {item['function']} — {item['file']}"):
                st.text(render_item(item, '', with_context=True).split('Source code:')[0])
                st.code(read_function_source(item), language='python')
            scores[item_id] = st.selectbox(f'Urgency for item {n}', range(1, 11),
                                          index=None, placeholder='Choose your own score',
                                          key=f'blind_{item_id}')
        independent = st.checkbox('I personally reviewed these items independently, without consulting model rankings.')
        submitted = st.form_submit_button('Save independent review')
    if submitted:
        if not rater.strip() or not independent or not scores or any(v is None for v in scores.values()):
            st.error('Enter a reviewer name, score every item, and confirm independent review.')
        else:
            save_rater(directory, rater, scores)
            st.success(f'Saved {len(scores)} ratings. The research team can now refresh the evaluation.')
            st.download_button('Download my review', json.dumps({'rater': rater, 'scores': scores}, indent=2),
                               file_name='expert_review.json', mime='application/json')
