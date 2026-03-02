import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Générateur Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Générateur de Planning Soignants (IA)")
st.markdown("**Règle Stricte : Jamais deux week-ends consécutifs.** Les manques sont comblés par des remplaçants.")

# --- 2. INTERFACE ---
col1, col2 = st.columns([1, 3])
with col1:
    st.subheader("⚙️ Paramètres")
    nb_semaines = st.number_input("Nombre de semaines :", min_value=2, max_value=12, value=4)
    date_debut = st.date_input("Date de début (Lundi) :", value=datetime.today())

with col2:
    st.subheader("👥 Équipe & Absences")
    data_base = pd.DataFrame({
        "Nom": [f"Soignant 100% (n°{i+1})" for i in range(15)] + [f"Soignant 80% (n°{i+1})" for i in range(3)],
        "Contrat (%)": [100]*15 + [80]*3,
        "Absences / Congés": [""] * 18
    })
    df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# --- FONCTION DATES ---
def get_abs(text, start_date, nb_days):
    indices = []
    if not text or pd.isna(text): return indices
    for part in str(text).replace(' ', '').split(','):
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
if st.button("🚀 GÉNÉRER LE PLANNING (ZÉRO FATIGUE)", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    noms_reels = df_equipe["Nom"].tolist()
    contrats = df_equipe["Contrat (%)"].tolist()
    abs_raw = df_equipe["Absences / Congés"].tolist()
    
    # On ajoute 10 "Remplaçants virtuels" pour boucher les trous
    noms_complets = noms_reels + [f"REMPLAÇANT {i+1}" for i in range(10)]
    nb_reels = len(noms_reels)
    total_staff = len(noms_complets)
    
    with st.spinner("L'IA protège vos soignants et cherche des remplaçants..."):
        jours = nb_semaines * 7
        shifts = ['M', 'A', 'C']
        model = cp_model.CpModel()
        x = {}
        for e in range(total_staff):
            for d in range(jours):
                for s in shifts:
                    x[(e, d, s)] = model.NewBoolVar(f's_{e}_{d}_{s}')
        
        # --- RÈGLES POUR LES SOIGNANTS RÉELS ---
        for e in range(nb_reels):
            # Contrat
            max_j = int((contrats[e] / 100) * 5 * nb_semaines)
            model.Add(sum(x[(e, d, s)] for d in range(jours) for s in shifts) == max_j)
            
            # Repos et cycles
            for d in range(jours): model.AddAtMostOne(x[(e, d, s)] for s in shifts)
            for d in range(jours - 1): model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not())
            
            we_vars = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                is_we = model.NewBoolVar(f'we_{e}_{w}')
                for s in shifts: model.Add(x[(e, sat, s)] == x[(e, sun, s)])
                model.AddMaxEquality(is_we, [x[(e, sat, s)] for s in shifts])
                we_vars.append(is_we)
                # Règle 6j/4j
                model.Add(sum(x[(e, d, s)] for d in range(w*7, w*7+7) for s in shifts) <= 4 + (2 * is_we))

            # 🛑 LA RÈGLE D'OR : JAMAIS DEUX WE CONSÉCUTIFS
            for w in range(nb_semaines - 1):
                model.Add(we_vars[w] + we_vars[w+1] <= 1)

            # Absences
            for d in get_abs(abs_raw[e], date_debut, jours):
                for s in shifts: model.Add(x[(e, d, s)] == 0)

        # --- RÈGLES POUR LES REMPLAÇANTS ---
        for e in range(nb_reels, total_staff):
            for d in range(jours):
                model.AddAtMostOne(x[(e, d, s)] for s in shifts)
                # Un remplaçant ne travaille QUE le week-end (pour aider l'équipe)
                if (d % 7 < 5): 
                    for s in shifts: model.Add(x[(e, d, s)] == 0)

        # --- QUOTAS FIXES DE L'EHPAD ---
        for d in range(jours):
            is_we = (d % 7 >= 5)
            m_r, a_r, c_r = (6, 3, 2) if is_we else (8, 4, 1)
            model.Add(sum(x[(e, d, 'M')] for e in range(total_staff)) == m_r)
            model.Add(sum(x[(e, d, 'A')] for e in range(total_staff)) == a_r)
            model.Add(sum(x[(e, d, 'C')] for e in range(total_staff)) == c_r)

        # --- OBJECTIF : UTILISER LE MOINS DE REMPLAÇANTS POSSIBLE ---
        # On pénalise l'utilisation des remplaçants pour que l'IA privilégie les vrais gens
        usage_remplacants = sum(x[(e, d, s)] for e in range(nb_reels, total_staff) for d in range(jours) for s in shifts)
        model.Minimize(usage_remplacants)

        solver = cp_model.CpSolver()
        status = solver.Solve(model)

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            st.success("✅ Planning généré ! Les repos consécutifs sont garantis.")
            
            planning_data, noms_finaux = [], []
            for e in range(total_staff):
                # On ne garde le remplaçant que s'il a au moins un jour de travail
                total_work = sum(solver.Value(x[(e, d, s)]) for d in range(jours) for s in shifts)
                if e < nb_reels or total_work > 0:
                    ligne, j_t, we_t = [], 0, 0
                    for d in range(jours):
                        poste = "Repos"
                        for s in shifts:
                            if solver.Value(x[(e, d, s)]) == 1: poste, j_t = s, j_t + 1
                        if d % 7 == 6 and poste != "Repos": we_t += 1
                        ligne.append(poste)
                    planning_data.append(ligne)
                    noms_finaux.append(noms_complets[e])

            df = pd.DataFrame(planning_data, columns=[(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours)], index=noms_finaux)
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, sheet_name='Planning')
                workbook, worksheet = writer.book, writer.sheets['Planning']
                f_m, f_a, f_c = workbook.add_format({'bg_color': '#D4EFDF'}), workbook.add_format({'bg_color': '#FCF3CF'}), workbook.add_format({'bg_color': '#FADBD8'})
                f_remp = workbook.add_format({'bg_color': '#E67E22', 'font_color': '#FFFFFF', 'bold': True}) # Orange pour les remplaçants
                f_we = workbook.add_format({'bg_color': '#EBEDEF'})
                
                worksheet.set_column('A:A', 25)
                for r in range(len(noms_finaux)):
                    is_remp = "REMPLAÇANT" in noms_finaux[r]
                    for c in range(jours):
                        val = df.iloc[r, c]
                        fmt = f_remp if is_remp and val != "Repos" else f_m if val == 'M' else f_a if val == 'A' else f_c if val == 'C' else None
                        if val == 'Repos' and (c % 7 >= 5): fmt = f_we
                        worksheet.write(r + 1, c + 1, val, fmt)
            
            st.download_button("📥 TÉLÉCHARGER LE PLANNING", output.getvalue(), "Planning_Repos_Garanti.xlsx")
        else:
            st.error("❌ Même avec des remplaçants, le calcul échoue. Vérifiez les quotas de semaine.")
