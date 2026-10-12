/* Native OpenCode form renderer. Selection never sends a chat prompt. */
(() => {
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  const button = (text, action) => {
    const node = el('button', 'action-button', text);
    node.type = 'button'; node.addEventListener('click', action);
    return node;
  };

  function mount(container, form, submit) {
    const card = el('article', 'native-question');
    card.append(el('h3', '', form.title || '待回答问题'));
    card.append(el('p', 'question-source', `来自会话：${form.session_title} · ${form.session_id}`));
    const fieldsBody = el('div', 'question-fields');
    const review = el('div', 'question-review hidden');
    const notice = el('p', 'form-hint'); notice.setAttribute('role', 'status');
    const choices = new Map();
    let answer = {};
    let unsupported = false;
    let sending = false;

    for (const field of form.fields || []) {
      const group = el('fieldset', 'question-field');
      group.append(el('legend', '', (field.title || field.key) + (field.required ? ' *' : '')));
      if (field.description) group.append(el('p', 'form-hint', field.description));
      const controls = [];
      const optionValues = [];
      const options = field.options || [];
      let customInput = null;
      let customChoice = null;
      const makeChoice = (value, label, description, type) => {
        const wrapper = el('label', 'question-choice');
        const input = el('input'); input.type = type;
        input.name = `${form.form_id}:${field.key}`;
        const body = el('span'); body.append(el('strong', '', label));
        if (description) body.append(el('small', '', description));
        input.addEventListener('change', updateVisibility);
        wrapper.append(input, body); group.append(wrapper);
        controls.push(input); optionValues.push(value);
        return input;
      };
      if (['string', 'multiselect'].includes(field.type) && options.length) {
        options.forEach(option => makeChoice(option.value, option.label, option.description,
          field.type === 'multiselect' ? 'checkbox' : 'radio'));
        if (field.custom) {
          customChoice = makeChoice(null, '自定义回答', '不使用上面选项，填写自己的决定。',
            field.type === 'multiselect' ? 'checkbox' : 'radio');
          customInput = el('textarea'); customInput.placeholder = '请输入自定义回答';
          customInput.setAttribute('aria-label', '自定义回答'); group.append(customInput);
        }
      } else if (field.type === 'boolean') {
        makeChoice(true, '是', '', 'radio'); makeChoice(false, '否', '', 'radio');
      } else if (['string', 'multiselect', 'number', 'integer'].includes(field.type)) {
        customInput = el(field.type === 'string' || field.type === 'multiselect' ? 'textarea' : 'input');
        if (['number', 'integer'].includes(field.type)) {
          customInput.type = 'number'; customInput.step = field.type === 'integer' ? '1' : 'any';
          if (typeof field.minimum === 'number') customInput.min = String(field.minimum);
          if (typeof field.maximum === 'number') customInput.max = String(field.maximum);
        }
        if (field.type === 'string') {
          if (field.maxLength != null) customInput.maxLength = field.maxLength;
          customInput.placeholder = field.placeholder || '请输入回答';
        } else if (field.type === 'multiselect') {
          customInput.placeholder = '每行输入一项';
        }
        customInput.setAttribute('aria-label', field.title || field.key);
        group.append(customInput);
      } else {
        unsupported = true;
        group.append(el('p', 'form-hint', '此问题包含外部交互字段，请在原 OpenCode 界面处理。'));
      }
      if (customInput) customInput.addEventListener('input', updateVisibility);
      choices.set(field.key, {field, group, controls, optionValues, customInput, customChoice});
      fieldsBody.append(group);
    }

    function readValue(entry) {
      const {field, controls, optionValues, customInput, customChoice} = entry;
      const checked = controls.map((input, i) => input.checked ? i : -1).filter(i => i >= 0);
      const custom = customInput?.value.trim();
      if (field.type === 'multiselect') {
        const values = checked.map(i => optionValues[i]).filter(value => value !== null);
        if (customInput && (!controls.length || customChoice?.checked) && custom) {
          values.push(...custom.split('\n').map(v => v.trim()).filter(Boolean));
        }
        return values.length ? [...new Set(values)] : undefined;
      }
      if (checked.length) {
        if (controls[checked[0]] === customChoice) return custom || undefined;
        return optionValues[checked[0]];
      }
      if (customInput && !controls.length && custom) {
        return ['number', 'integer'].includes(field.type) ? Number(custom) : custom;
      }
      return undefined;
    }

    function readAnswer() {
      const values = {};
      for (const [key, entry] of choices) {
        const value = readValue(entry);
        if (value !== undefined) values[key] = value;
      }
      for (const [key, entry] of choices) {
        const visible = !entry.field.hidden && (entry.field.when || []).every(condition =>
          condition.op === 'eq' ? values[condition.key] === condition.value : values[condition.key] !== condition.value);
        entry.group.hidden = !visible;
        if (!visible) delete values[key];
      }
      return values;
    }

    function updateVisibility() { readAnswer(); }
    function validate(values) {
      for (const [key, {field, group}] of choices) {
        if (group.hidden) continue;
        const value = values[key];
        if (field.required && value === undefined) throw new Error(`请回答：${field.title || key}`);
        if (value === undefined) continue;
        if (field.type === 'string') {
          if (value.length < (field.minLength || 0) || value.length > (field.maxLength ?? 20000)) throw new Error('回答长度不符合要求。');
        }
        if (field.type === 'multiselect') {
          if (value.length < (field.minItems || 0) || value.length > (field.maxItems ?? 100)) throw new Error('请选择符合数量要求的选项。');
        }
        if (['number', 'integer'].includes(field.type)) {
          if (!Number.isFinite(value) || (field.type === 'integer' && !Number.isInteger(value))) throw new Error('请输入有效数值。');
          if ((typeof field.minimum === 'number' && value < field.minimum) || (typeof field.maximum === 'number' && value > field.maximum)) throw new Error('数值不在允许范围内。');
        }
      }
    }
    const choose = button('下一步：核对回答', () => {
      try {
        answer = readAnswer(); validate(answer);
        review.replaceChildren(el('h4', '', '请确认以下回答'));
        for (const [key, value] of Object.entries(answer)) {
          const field = choices.get(key).field;
          const label = v => field.options?.find(o => o.value === v)?.label ?? (v === true ? '是' : v === false ? '否' : String(v));
          review.append(el('p', '', `${field.title || key}：${Array.isArray(value) ? value.map(label).join('、') : label(value)}`));
        }
        if (!Object.keys(answer).length) review.append(el('p', '', '未填写可选字段。'));
        review.append(el('p', 'form-hint', '确认后将回答原生提问，不会发送普通聊天消息。'), edit, confirm);
        fieldsBody.classList.add('hidden'); review.classList.remove('hidden'); choose.classList.add('hidden');
        notice.textContent = '';
      } catch (error) { notice.textContent = error.message; }
    });
    const edit = button('返回修改', () => {
      review.classList.add('hidden'); fieldsBody.classList.remove('hidden'); choose.classList.remove('hidden');
    });
    const confirm = button('确认提交回答', async () => {
      if (sending) return;
      sending = true; confirm.disabled = true; edit.disabled = true;
      notice.textContent = '正在提交…';
      try {
        await submit(form, answer);
        review.replaceChildren(el('p', '', '回答已提交到 OpenCode。'));
        notice.textContent = '原会话已收到结构化回答。';
      } catch (error) {
        notice.textContent = error.message;
        confirm.disabled = false; edit.disabled = false;
      } finally { sending = false; }
    });
    confirm.classList.add('primary');
    choose.disabled = unsupported;
    card.append(fieldsBody, review, notice, choose);
    container.append(card);
    updateVisibility();
    return card;
  }
  window.OpenCodeDecisions = {mount};
})();
