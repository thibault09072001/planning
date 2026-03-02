import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Générateur Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Générateur de Planning Soignants (IA)")
st.markdown("Version **Flexible** : L'IA optimise le confort mais garantit toujours un résultat.")

# --- 2. INTERFACE ---
col1, col2 = st.columns([1, 2])
with col1:
    st.subheader("⚙️ Paramètres")
    nb_semaines = st.number_input("Nombre de semaines :", min_value=2, max_value=12, value=4)
    date_debut = st.date_input("Date de début (Lundi) :", value=datetime.today())

with col2:
    st.subheader("👥 Équipe Soignante")
    data_base = pd.DataFrame({
        "Nom": [f"Soignant 100% (n°{i+1})" for i in range(15)] + [f"Soignant 80% (n°{i+1})" for i in range(3)],
        "Contrat (%)": [100]*15 + [80]*3
    })
    df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# --- 3. MOTEUR IA ---
if st.button("🚀 GÉNÉRER LE PLANNING (MODE FLEXIBLE)", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    df_equipe = df_equipe[df_equipe['Contrat (%)'] > 0]
    
    noms_salaries = df_equipe["Nom"].tolist()
    contrats = df_equipe["Contrat (%)"].tolist()
    total_salaries = len(noms_salaries)
    
    if total_salaries == 0:
        st.error("⚠️ Tableau vide.")
    else:
        with st.spinner("L'IA optimise le planning..."):
            jours = nb_semaines * 7
            max_jours = [int((c / 100) * 5 * nb_semaines) for c in contrats]
            shifts = ['M', 'A', 'C']
            
            model = cp_model.CpModel()
            x = {}
            for e in range(total_salaries):
                for d in range(jours):
                    for s in shifts:
                        x[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')
            
            # --- CONTRAINTES STRICTES (INDÉPASSABLES) ---
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

            # --- OPTIMISATION DU CONFORT (SOUPLY) ---
            penalites = []
            
            for e in range(total_salaries):
                we_vars = []
                for w in range(nb_semaines):
                    w_var = model.NewBoolVar(f'we_{e}_{w}')
                    model.AddMaxEquality(w_var, [x[(e, w*7+5, s)] for s in shifts])
                    we_vars.append(w_var)
                
                # 1. Pénalité si 3 WE de suite
                for w in range(nb_semaines - 2):
                    p3 = model.NewBoolVar(f'p3_{e}_{w}')
                    model.Add(we_vars[w] + we_vars[w+1] + we_vars[w+2] <= 2 + p3)
                    penalites.append(p3 * 100) # Grosse punition pour l'IA
                
                # 2. Pénalité si 2 WE de suite (pour ceux à qui on veut donner une pause)
                for w in range(nb_semaines - 1):
                    p2 = model.NewBoolVar(f'p2_{e}_{w}')
                    model.Add(we_vars[w] + we_vars[w+1] <= 1 + p2)
                    penalites.append(p2 * 10) # Punition moyenne

            model.Minimize(sum(penalites))

            # --- CALCUL ---
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 30.0
            status = solver.Solve(model)

            if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                st.success("✅ Planning généré !")
                
                planning_data, audit_data = [], []
                for e in range(total_salaries):
                    ligne, j_travailles, we_count = [], 0, 0
                    current_we_travailles = []
                    for d in range(jours):
                        poste = "Repos"
                        for s in shifts:
                            if solver.Value(x[(e, d, s)]) == 1:
                                poste = s
                                j_travailles += 1
                        if d % 7 == 6:
                            is_on = 1 if poste != "Repos" else 0
                            current_we_travailles.append(is_on)
                            we_count += is_on
                        ligne.append(poste)
                    
                    # Audit des consécutifs
                    cons_txt = ""
                    for w in range(len(current_we_travailles)-1):
                        if current_we_travailles[w] == 1 and current_we_travailles[w+1] == 1:
                            cons_txt = " | ⚠️ WE consécutifs"
                    
                    planning_data.append(ligne)
                    audit_data.append(f"✅ {j_travailles}/{max_jours[e]} jrs | {we_count} WE{cons_txt}")

                colonnes = [(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours)]
                df = pd.DataFrame(planning_data, columns=colonnes, index=noms_salaries)
                df['AUDIT'] = audit_data
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df.to_excel(writer, sheet_name='Planning')
                    workbook, worksheet = writer.book, writer.sheets['Planning']
                    f_m = workbook.add_format({'bg_color': '#D4EFDF', 'align': 'center'})
                    f_a = workbook.add_format({'bg_color': '#FCF3CF', 'align': 'center'})
                    f_c = workbook.add_format({'bg_color': '#FADBD8', 'align': 'center'})
                    f_we = workbook.add_format({'bg_color': '#EBEDEF', 'align': 'center'})
                    
                    worksheet.set_column('A:A', 25)
                    worksheet.set_column(1, jours, 8)
                    worksheet.set_column(jours + 1, jours + 1, 40)

                    for r in range(total_salaries):
                        for c in range(jours):
                            val = df.iloc[r, c]
                            fmt = f_m if val == 'M' else f_a if val == 'A' else f_c if val == 'C' else None
                            if val == 'Repos' and (c % 7 >= 5): fmt = f_we
                            worksheet.write(r + 1, c + 1, val, fmt)
                        worksheet.write(r + 1, jours + 1, df.iloc[r, jours])

                st.download_button("📥 TÉLÉCHARGER EXCEL", output.getvalue(), f"Planning_{date_debut}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                st.error("❌ Même en mode flexible, les quotas sont impossibles avec cet effectif. Vérifie tes chiffres !")
