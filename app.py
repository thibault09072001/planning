import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Générateur Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Générateur de Planning Soignants (IA)")
st.markdown("Version **Sécurité Totale** : Max 3 WE, Rythme 6j/4j et gestion des congés.")

# --- 2. INTERFACE ---
col1, col2 = st.columns([1, 3])
with col1:
    st.subheader("⚙️ Paramètres")
    nb_semaines = st.number_input("Nombre de semaines :", min_value=2, max_value=12, value=4)
    date_debut = st.date_input("Date de début (Lundi) :", value=datetime.today())

with col2:
    st.subheader("👥 Équipe & Absences")
    st.info("Format absences : '01/05' ou '01/05-05/05'. Max 3 WE travaillés par personne.")
    
    data_base = pd.DataFrame({
        "Nom": [f"Soignant 100% (n°{i+1})" for i in range(15)] + [f"Soignant 80% (n°{i+1})" for i in range(3)],
        "Contrat (%)": [100]*15 + [80]*3,
        "Absences / Congés": [""] * 18
    })
    df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# --- FONCTION DE PARSING DES DATES ---
def get_absence_indices(text, start_date, nb_days):
    indices = []
    if not text or pd.isna(text): return indices
    parts = str(text).replace(' ', '').split(',')
    for part in parts:
        try:
            if '-' in part:
                d1_s, d2_s = part.split('-')
                d1 = datetime.strptime(d1_s + f"/{start_date.year}", "%d/%m/%Y").date()
                d2 = datetime.strptime(d2_s + f"/{start_date.year}", "%d/%m/%Y").date()
                for i in range((d2 - d1).days + 1):
                    diff = (d1 + timedelta(days=i) - start_date).days
                    if 0 <= diff < nb_days: indices.append(diff)
            else:
                day = datetime.strptime(part + f"/{start_date.year}", "%d/%m/%Y").date()
                diff = (day - start_date).days
                if 0 <= diff < nb_days: indices.append(diff)
        except: continue
    return list(set(indices))

# --- 3. MOTEUR IA ---
if st.button("🚀 GÉNÉRER LE PLANNING (MAX 3 WE)", type="primary", use_container_width=True):
    
    # Nettoyage
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    noms_salaries = df_equipe["Nom"].tolist()
    contrats = df_equipe["Contrat (%)"].tolist()
    abs_raw = df_equipe["Absences / Congés"].tolist()
    total_salaries = len(noms_salaries)
    
    if total_salaries == 0:
        st.error("⚠️ L'équipe est vide.")
    else:
        with st.spinner("L'IA verrouille les week-ends et calcule le cycle 6j/4j..."):
            jours = nb_semaines * 7
            max_jours_contrat = [int((c / 100) * 5 * nb_semaines) for c in contrats]
            shifts = ['M', 'A', 'C']
            
            model = cp_model.CpModel()
            x = {}
            for e in range(total_salaries):
                for d in range(jours):
                    for s in shifts:
                        x[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')
            
            # --- RÈGLES PAR SOIGNANT ---
            for e in range(total_salaries):
                # 1. Contrat exact
                model.Add(sum(x[(e, d, s)] for d in range(jours) for s in shifts) == max_jours_contrat[e])
                
                # 2. Max 1 poste par jour
                for d in range(jours):
                    model.AddAtMostOne(x[(e, d, s)] for s in shifts)
                
                # 3. Interdiction A -> M
                for d in range(jours - 1):
                    model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not())
                
                # 4. Week-ends et Plafond strict
                we_vars = []
                for w in range(nb_semaines):
                    sat, sun = w * 7 + 5, w * 7 + 6
                    is_we_worked = model.NewBoolVar(f'is_we_worked_{e}_{w}')
                    
                    # Samedi = Dimanche (Bloc)
                    for s in shifts:
                        model.Add(x[(e, sat, s)] == x[(e, sun, s)])
                    
                    # Détection si WE travaillé
                    model.AddMaxEquality(is_we_worked, [x[(e, sat, s)] for s in shifts])
                    we_vars.append(is_we_worked)
                    
                    # RÈGLE 6j/4j : Max 6j si WE travaillé, Max 4j sinon
                    jours_semaine = range(w * 7, w * 7 + 7)
                    total_semaine = sum(x[(e, d, s)] for d in jours_semaine for s in shifts)
                    model.Add(total_semaine <= 4 + (2 * is_we_worked))

                # 🛑 LE VERROU : MAXIMUM 3 WEEK-ENDS SUR 4
                model.Add(sum(we_vars) <= 3)

                # 5. Absences
                abs_indices = get_absence_indices(abs_raw[e], date_debut, jours)
                for d in abs_indices:
                    for s in shifts: model.Add(x[(e, d, s)] == 0)

            # --- QUOTAS EHPAD ---
            for d in range(jours):
                is_we = (d % 7 >= 5)
                m_req, a_req, c_req = (6, 3, 2) if is_we else (8, 4, 1)
                model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) >= m_req)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == a_req)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == c_req)

            # --- OPTIMISATION DU CONFORT (SOUPLY) ---
            penalites = []
            for e in range(total_salaries):
                for w in range(nb_semaines - 1):
                    # On préfère éviter les WE de suite quand c'est possible
                    p_consec = model.NewBoolVar(f'p_consec_{e}_{w}')
                    model.Add(we_vars[w] + we_vars[w+1] <= 1 + p_consec)
                    penalites.append(p_consec * 50)
            
            model.Minimize(sum(penalites))

            # --- CALCUL ---
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 40.0
            status = solver.Solve(model)

            if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                st.success("✅ Planning conforme généré (Max 3 WE respecté).")
                
                planning_data, audit_data = [], []
                for e in range(total_salaries):
                    ligne, j_travailles, we_count = [], 0, 0
                    abs_idx = get_absence_indices(abs_raw[e], date_debut, jours)
                    for d in range(jours):
                        poste = "CONGÉ" if d in abs_idx else "Repos"
                        for s in shifts:
                            if solver.Value(x[(e, d, s)]) == 1:
                                poste, j_travailles = s, j_travailles + 1
                        if d % 7 == 6 and poste not in ["Repos", "CONGÉ"]: we_count += 1
                        ligne.append(poste)
                    planning_data.append(ligne)
                    audit_data.append(f"✅ {j_travailles}j | {we_count} WE")

                df = pd.DataFrame(planning_data, columns=[(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours)], index=noms_salaries)
                df['AUDIT'] = audit_data
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df.to_excel(writer, sheet_name='Planning')
                    workbook, worksheet = writer.book, writer.sheets['Planning']
                    f_m = workbook.add_format({'bg_color': '#D4EFDF', 'align': 'center'})
                    f_a = workbook.add_format({'bg_color': '#FCF3CF', 'align': 'center'})
                    f_c = workbook.add_format({'bg_color': '#FADBD8', 'align': 'center'})
                    f_abs = workbook.add_format({'bg_color': '#000000', 'font_color': '#FFFFFF', 'align': 'center'})
                    f_we = workbook.add_format({'bg_color': '#EBEDEF', 'align': 'center'})
                    
                    worksheet.set_column('A:A', 25)
                    worksheet.set_column(1, jours, 8)
                    for r in range(total_salaries):
                        for c in range(jours):
                            val = df.iloc[r, c]
                            fmt = f_m if val == 'M' else f_a if val == 'A' else f_c if val == 'C' else f_abs if val == 'CONGÉ' else None
                            if val == 'Repos' and (c % 7 >= 5): fmt = f_we
                            worksheet.write(r + 1, c + 1, val, fmt)
                
                st.download_button("📥 TÉLÉCHARGER LE PLANNING", output.getvalue(), "Planning_Final.xlsx")
            else:
                st.error("❌ Impossible de respecter la limite des 3 WE avec cet effectif et ces absences. Embauchez ou réduisez les congés !")
