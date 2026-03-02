import streamlit as st
import pandas as pd
import io
import math
import re
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Générateur Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Générateur de Planning Soignants (IA)")
st.markdown("Version **Expert** : Gestion des congés et absences intégrée.")

# --- 2. INTERFACE ---
col1, col2 = st.columns([1, 3])
with col1:
    st.subheader("⚙️ Paramètres")
    nb_semaines = st.number_input("Nombre de semaines :", min_value=2, max_value=12, value=4)
    date_debut = st.date_input("Date de début (Lundi) :", value=datetime.today())

with col2:
    st.subheader("👥 Équipe & Absences")
    st.info("Format absences : '01/05' ou '01/05-05/05'. Séparez par des virgules si plusieurs.")
    
    # On ajoute la colonne "Absences" au tableau de base
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
    
    # Nettoyage et découpage par virgule
    parts = str(text).replace(' ', '').split(',')
    for part in parts:
        try:
            if '-' in part: # Cas d'une plage : 01/05-05/05
                d1_str, d2_str = part.split('-')
                d1 = datetime.strptime(d1_str + f"/{start_date.year}", "%d/%m/%Y").date()
                d2 = datetime.strptime(d2_str + f"/{start_date.year}", "%d/%m/%Y").date()
                delta = (d2 - d1).days
                for i in range(delta + 1):
                    day = d1 + timedelta(days=i)
                    diff = (day - start_date).days
                    if 0 <= diff < nb_days: indices.append(diff)
            else: # Cas d'un jour unique : 01/05
                day = datetime.strptime(part + f"/{start_date.year}", "%d/%m/%Y").date()
                diff = (day - start_date).days
                if 0 <= diff < nb_days: indices.append(diff)
        except: continue # Ignore les erreurs de frappe
    return list(set(indices))

# --- 3. MOTEUR IA ---
if st.button("🚀 GÉNÉRER LE PLANNING AVEC CONGÉS", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_salaries = df_equipe["Nom"].tolist()
    contrats = df_equipe["Contrat (%)"].tolist()
    absences_raw = df_equipe["Absences / Congés"].tolist()
    total_salaries = len(noms_salaries)
    
    if total_salaries == 0:
        st.error("⚠️ Tableau vide.")
    else:
        with st.spinner("L'IA calcule en tenant compte des congés..."):
            jours = nb_semaines * 7
            max_jours = [int((c / 100) * 5 * nb_semaines) for c in contrats]
            shifts = ['M', 'A', 'C']
            
            model = cp_model.CpModel()
            x = {}
            for e in range(total_salaries):
                for d in range(jours):
                    for s in shifts:
                        x[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')
            
            # --- APPLICATION DES CONGÉS ---
            for e in range(total_salaries):
                abs_indices = get_absence_indices(absences_raw[e], date_debut, jours)
                for d in abs_indices:
                    # Le soignant ne peut travailler aucun shift ces jours-là
                    for s in shifts:
                        model.Add(x[(e, d, s)] == 0)
                
                # Ajustement du contrat : On réduit le nombre de jours à travailler 
                # (Optionnel : si tu veux que les congés comptent dans les heures, ne change pas max_jours)
                # Ici, on garde max_jours fixe pour que le soignant fasse ses heures sur ses jours de présence.

            # --- CONTRAINTES STRICTES ---
            for e in range(total_salaries):
                for d in range(jours):
                    model.AddAtMostOne(x[(e, d, s)] for s in shifts)
                model.Add(sum(x[(e, d, s)] for d in range(jours) for s in shifts) == max_jours[e])
                for d in range(jours - 1):
                    model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not())
                for w in range(nb_semaines):
                    sat, sun = w * 7 + 5, w * 7 + 6
                    for s in shifts:
                        model.Add(x[(e, sat, s)] == x[(e, sun, s)])

            for d in range(jours):
                is_we = (d % 7 >= 5)
                m_req, a_req, c_req = (6, 3, 2) if is_we else (8, 4, 1)
                model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) >= m_req)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == a_req)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == c_req)

            # --- OPTIMISATION DU CONFORT ---
            penalites = []
            for e in range(total_salaries):
                we_vars = []
                for w in range(nb_semaines):
                    w_var = model.NewBoolVar(f'we_{e}_{w}')
                    model.AddMaxEquality(w_var, [x[(e, w*7+5, s)] for s in shifts])
                    we_vars.append(w_var)
                for w in range(nb_semaines - 2):
                    p3 = model.NewBoolVar(f'p3_{e}_{w}')
                    model.Add(we_vars[w] + we_vars[w+1] + we_vars[w+2] <= 2 + p3)
                    penalites.append(p3 * 100)
                for w in range(nb_semaines - 1):
                    p2 = model.NewBoolVar(f'p2_{e}_{w}')
                    model.Add(we_vars[w] + we_vars[w+1] <= 1 + p2)
                    penalites.append(p2 * 10)

            model.Minimize(sum(penalites))

            # --- CALCUL ---
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 40.0
            status = solver.Solve(model)

            if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                st.success("✅ Planning généré avec succès !")
                
                planning_data, audit_data = [], []
                for e in range(total_salaries):
                    ligne, j_travailles, we_count = [], 0, 0
                    abs_indices = get_absence_indices(absences_raw[e], date_debut, jours)
                    for d in range(jours):
                        if d in abs_indices:
                            poste = "CONGÉ"
                        else:
                            poste = "Repos"
                            for s in shifts:
                                if solver.Value(x[(e, d, s)]) == 1:
                                    poste, j_travailles = s, j_travailles + 1
                        
                        if d % 7 == 6 and poste != "CONGÉ" and poste != "Repos": we_count += 1
                        ligne.append(poste)
                    
                    planning_data.append(ligne)
                    audit_data.append(f"✅ {j_travailles}/{max_jours[e]} jrs | {we_count} WE")

                colonnes = [(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours)]
                df = pd.DataFrame(planning_data, columns=colonnes, index=noms_salaries)
                df['AUDIT'] = audit_data
                
                # --- EXCEL ---
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df.to_excel(writer, sheet_name='Planning')
                    workbook, worksheet = writer.book, writer.sheets['Planning']
                    f_m = workbook.add_format({'bg_color': '#D4EFDF', 'align': 'center'})
                    f_a = workbook.add_format({'bg_color': '#FCF3CF', 'align': 'center'})
                    f_c = workbook.add_format({'bg_color': '#FADBD8', 'align': 'center'})
                    f_abs = workbook.add_format({'bg_color': '#000000', 'font_color': '#FFFFFF', 'align': 'center', 'bold': True})
                    f_we = workbook.add_format({'bg_color': '#EBEDEF', 'align': 'center'})
                    
                    worksheet.set_column('A:A', 25)
                    worksheet.set_column(1, jours, 8)

                    for r in range(total_salaries):
                        for c in range(jours):
                            val = df.iloc[r, c]
                            fmt = f_m if val == 'M' else f_a if val == 'A' else f_c if val == 'C' else f_abs if val == 'CONGÉ' else None
                            if val == 'Repos' and (c % 7 >= 5): fmt = f_we
                            worksheet.write(r + 1, c + 1, val, fmt)
                
                st.download_button("📥 TÉLÉCHARGER LE PLANNING", output.getvalue(), "Planning_EHPAD.xlsx")
            else:
                st.error("❌ Impossible de respecter les quotas avec ces absences. Trop de soignants absents en même temps !")
